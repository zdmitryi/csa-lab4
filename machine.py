import struct
import sys
from typing import Protocol

from isa import (
    OP_ADC,
    OP_ADD,
    OP_ADDI,
    OP_AND,
    OP_CALL,
    OP_CMP,
    OP_CMPI,
    OP_DI,
    OP_DIV,
    OP_EI,
    OP_HALT,
    OP_IN,
    OP_IRET,
    OP_JG,
    OP_JGE,
    OP_JL,
    OP_JLE,
    OP_JMP,
    OP_JNZ,
    OP_JZ,
    OP_LD,
    OP_LD_IND,
    OP_LDI,
    OP_MOD,
    OP_MOV,
    OP_MUL,
    OP_NOP,
    OP_NOT,
    OP_OR,
    OP_OUT,
    OP_POLY,
    OP_POP,
    OP_PUSH,
    OP_RET,
    OP_SBC,
    OP_ST,
    OP_ST_IND,
    OP_SUB,
    OP_SUBI,
    OP_XOR,
    WORD_BYTES,
)

DMEM_SIZE = 0x100000
DMEM_WORDS = DMEM_SIZE // WORD_BYTES
STACK_TOP = DMEM_SIZE
DATA_START = 0x0100


def s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def trunc_div(a: int, b: int) -> int:
    if b == 0:
        return 0
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def trunc_mod(a: int, b: int) -> int:
    if b == 0:
        return 0
    return a - trunc_div(a, b) * b


class ProcessorError(Exception):
    pass


class ProcessorModel(Protocol):
    schedule: list[tuple[int, int]]
    ticks: int
    intr_count: int
    input_overrun: int
    error: str | None
    log: list[str]

    def run(self, limit: int = 10_000_000) -> str: ...


def load_binary(filename: str) -> tuple[int, list[int], list[int]]:
    with open(filename, "rb") as f:
        data = f.read()
    if len(data) < 8:
        raise ProcessorError(f"{filename}: файл слишком короткий")
    entry = struct.unpack(">I", data[0:4])[0]
    data_size = struct.unpack(">I", data[4:8])[0]
    offset = 8
    imem: list[int] = []
    code_end = len(data) - data_size * 4
    while offset < code_end:
        imem.append(struct.unpack(">I", data[offset : offset + 4])[0])
        offset += 4
    dmem: list[int] = [0] * DMEM_WORDS
    d_ptr = DATA_START // WORD_BYTES
    while offset < len(data):
        if d_ptr >= DMEM_WORDS:
            raise ProcessorError("данные не помещаются в DMEM")
        dmem[d_ptr] = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        d_ptr += 1
    return entry, imem, dmem


def parse_schedule(text: str) -> list[tuple[int, int]]:
    events: list[tuple[int, int]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tick_s, value_s = line.split(None, 1)
        tick = int(tick_s)
        value_s = value_s.strip()
        if value_s == "EOF":
            value = -1
        elif value_s == "\\n":
            value = 10
        elif value_s == "\\t":
            value = 9
        elif value_s == "\\s":
            value = 32
        elif len(value_s) == 1:
            value = ord(value_s)
        else:
            value = int(value_s)
        events.append((tick, value))
    events.sort(key=lambda e: e[0])
    return events


class Processor:
    def __init__(self, entry: int, imem: list[int], dmem: list[int]) -> None:
        self.pc: int = entry
        self.imem: list[int] = imem
        self.dmem: list[int] = dmem
        self.regs: list[int] = [0] * 4
        self.sp: int = STACK_TOP
        self.flags: dict[str, int] = {"Z": 0, "N": 0, "C": 0}
        self.output: str = ""
        self.halted: bool = False
        self.error: str | None = None
        self.ticks: int = 0
        self.state: str = "FETCH1"
        self.ir: int = 0
        self.log: list[str] = []

        self.intr_enabled: bool = False
        self.intr_pending: bool = False
        self.ctx_stack: list[tuple[int, dict[str, int], bool]] = []
        self.port_in: int | None = None
        self.port_valid: bool = False
        self.input_overrun: int = 0
        self.schedule: list[tuple[int, int]] = []
        self.schedule_pos: int = 0
        self.intr_count: int = 0

    @property
    def in_progress(self) -> bool:
        return bool(self.ctx_stack)

    def mem_read(self, addr: int) -> int:
        addr &= 0xFFFFFFFF
        if addr % WORD_BYTES:
            raise ProcessorError(
                f"невыровненное чтение DMEM: 0x{addr:X} (PC=0x{self.pc:04X})"
            )
        if not (0 <= addr < DMEM_SIZE):
            raise ProcessorError(
                f"чтение DMEM вне диапазона: 0x{addr:X} (PC=0x{self.pc:04X})"
            )
        return self.dmem[addr // WORD_BYTES]

    def mem_write(self, addr: int, value: int) -> None:
        addr &= 0xFFFFFFFF
        if addr % WORD_BYTES:
            raise ProcessorError(
                f"невыровненная запись в DMEM: 0x{addr:X} (PC=0x{self.pc:04X})"
            )
        if not (0 <= addr < DMEM_SIZE):
            raise ProcessorError(
                f"запись в DMEM вне диапазона: 0x{addr:X} (PC=0x{self.pc:04X})"
            )
        self.dmem[addr // WORD_BYTES] = value & 0xFFFFFFFF

    def push(self, value: int) -> None:
        if self.sp < WORD_BYTES:
            raise ProcessorError("переполнение стека")
        self.sp -= WORD_BYTES
        self.mem_write(self.sp, value)

    def pop(self) -> int:
        if self.sp >= STACK_TOP:
            raise ProcessorError(f"POP при пустом стеке (PC=0x{self.pc:04X})")
        value = self.mem_read(self.sp)
        self.sp += WORD_BYTES
        return value

    def deliver_input(self, value: int) -> None:
        if self.port_valid:
            self.input_overrun += 1
            return
        self.port_in = value
        self.port_valid = True
        self.intr_pending = True

    def check_schedule(self) -> None:
        while (
            self.schedule_pos < len(self.schedule)
            and self.schedule[self.schedule_pos][0] <= self.ticks
        ):
            _, value = self.schedule[self.schedule_pos]
            self.schedule_pos += 1
            self.deliver_input(value)

    def accept_interrupt(self) -> None:
        if not (self.intr_pending and self.intr_enabled):
            return
        vector = self.imem[0] if self.imem else 0
        self.intr_pending = False
        if vector == 0:
            return
        self.ctx_stack.append((self.pc, dict(self.flags), self.intr_enabled))
        self.intr_enabled = False
        self.pc = vector
        self.intr_count += 1
        self.log.append(
            f"tick={self.ticks:4d} | INTR accepted -> PC=0x{vector:04X} "
            f"depth={len(self.ctx_stack)}"
        )

    def set_flags(self, result: int, carry: bool) -> None:
        self.flags["Z"] = 1 if (result & 0xFFFFFFFF) == 0 else 0
        self.flags["N"] = 1 if (result & 0x80000000) else 0
        self.flags["C"] = 1 if carry else 0

    def dump_state(self) -> None:
        regs_str = " ".join(f"R{i}={self.regs[i]}" for i in range(4))
        flags_str = f"Z={self.flags['Z']} N={self.flags['N']} C={self.flags['C']}"
        intr_str = (
            f"INTR={'on' if self.in_progress else 'off'}"
            f" pend={1 if self.intr_pending else 0}"
        )
        line = (
            f"tick={self.ticks:4d} | state={self.state:7s} | "
            f"PC=0x{self.pc:04X} | IR=0x{self.ir:08X} | {regs_str} | "
            f"{flags_str} | {intr_str}"
        )
        self.log.append(line)

    def step(self) -> bool:
        if self.halted:
            return False

        if self.state == "FETCH1":
            self.check_schedule()
            self.accept_interrupt()
            self.state = "FETCH2"

        elif self.state == "FETCH2":
            if self.pc % WORD_BYTES:
                raise ProcessorError(f"невыровненный PC=0x{self.pc:04X}")
            if self.pc < 0 or self.pc + WORD_BYTES > len(self.imem) * WORD_BYTES:
                self.halted = True
                return False
            self.ir = self.imem[self.pc // WORD_BYTES]
            self.state = "DECODE"

        elif self.state == "DECODE":
            self.state = "EXEC"

        elif self.state == "EXEC":
            instr = self.ir
            op = (instr >> 24) & 0xFF
            rd = (instr >> 20) & 0xF
            rs = (instr >> 16) & 0xF
            imm = instr & 0xFFFFF
            if imm & (1 << 19):
                imm = imm | 0xFFF00000
            addr = instr & 0xFFFFF
            jaddr = instr & 0xFFFFFF

            if rd > 3:
                raise ProcessorError(
                    f"недопустимый номер регистра rd={rd} (PC=0x{self.pc:04X})"
                )

            pc_updated = False

            if op == OP_HALT:
                self.halted = True
                return False

            if op == OP_NOP:
                pass

            elif op == OP_LDI:
                self.regs[rd] = imm

            elif op == OP_LD:
                self.regs[rd] = self.mem_read(addr)

            elif op == OP_LD_IND:
                self.regs[rd] = self.mem_read(self.regs[rs])

            elif op == OP_ST:
                self.mem_write(addr, self.regs[rd])

            elif op == OP_ST_IND:
                self.mem_write(self.regs[rd], self.regs[rs])

            elif op == OP_ADD:
                u = self.regs[rd] + self.regs[rs]
                self.set_flags(u, u > 0xFFFFFFFF)
                self.regs[rd] = u & 0xFFFFFFFF

            elif op == OP_ADDI:
                u = self.regs[rd] + (imm & 0xFFFFFFFF)
                self.set_flags(u, u > 0xFFFFFFFF)
                self.regs[rd] = u & 0xFFFFFFFF

            elif op == OP_SUB:
                u = self.regs[rd] - self.regs[rs]
                self.set_flags(u, self.regs[rd] < self.regs[rs])
                self.regs[rd] = u & 0xFFFFFFFF

            elif op == OP_SUBI:
                u = self.regs[rd] - (imm & 0xFFFFFFFF)
                self.set_flags(u, self.regs[rd] < (imm & 0xFFFFFFFF))
                self.regs[rd] = u & 0xFFFFFFFF

            elif op == OP_MUL:
                r = s32(self.regs[rd]) * s32(self.regs[rs])
                self.set_flags(r, False)
                self.regs[rd] = r & 0xFFFFFFFF

            elif op == OP_DIV:
                self.regs[rd] = (
                    trunc_div(s32(self.regs[rd]), s32(self.regs[rs])) & 0xFFFFFFFF
                )

            elif op == OP_MOD:
                self.regs[rd] = (
                    trunc_mod(s32(self.regs[rd]), s32(self.regs[rs])) & 0xFFFFFFFF
                )

            elif op == OP_ADC:
                u = self.regs[rd] + self.regs[rs] + self.flags["C"]
                self.set_flags(u, u > 0xFFFFFFFF)
                self.regs[rd] = u & 0xFFFFFFFF

            elif op == OP_SBC:
                sub = self.regs[rs] + self.flags["C"]
                u = self.regs[rd] - sub
                self.set_flags(u, self.regs[rd] < sub)
                self.regs[rd] = u & 0xFFFFFFFF

            elif op == OP_CMP:
                u = self.regs[rd] - self.regs[rs]
                self.set_flags(u, self.regs[rd] < self.regs[rs])

            elif op == OP_CMPI:
                u = self.regs[rd] - (imm & 0xFFFFFFFF)
                self.set_flags(u, self.regs[rd] < (imm & 0xFFFFFFFF))

            elif op == OP_AND:
                self.regs[rd] = (self.regs[rd] & self.regs[rs]) & 0xFFFFFFFF

            elif op == OP_OR:
                self.regs[rd] = (self.regs[rd] | self.regs[rs]) & 0xFFFFFFFF

            elif op == OP_XOR:
                self.regs[rd] = (self.regs[rd] ^ self.regs[rs]) & 0xFFFFFFFF

            elif op == OP_NOT:
                self.regs[rd] = (~self.regs[rd]) & 0xFFFFFFFF

            elif op == OP_PUSH:
                self.push(self.regs[rd])

            elif op == OP_POP:
                self.regs[rd] = self.pop()

            elif op == OP_MOV:
                self.regs[rd] = self.regs[rs]

            elif op == OP_JMP:
                self.pc = jaddr
                pc_updated = True

            elif op == OP_JZ:
                if self.flags["Z"]:
                    self.pc = jaddr
                    pc_updated = True

            elif op == OP_JNZ:
                if not self.flags["Z"]:
                    self.pc = jaddr
                    pc_updated = True

            elif op == OP_JL:
                if self.flags["N"]:
                    self.pc = jaddr
                    pc_updated = True

            elif op == OP_JG:
                if not self.flags["N"] and not self.flags["Z"]:
                    self.pc = jaddr
                    pc_updated = True

            elif op == OP_JLE:
                if self.flags["N"] or self.flags["Z"]:
                    self.pc = jaddr
                    pc_updated = True

            elif op == OP_JGE:
                if not self.flags["N"]:
                    self.pc = jaddr
                    pc_updated = True

            elif op == OP_CALL:
                self.push(self.pc + WORD_BYTES)
                self.pc = jaddr
                pc_updated = True

            elif op == OP_RET:
                self.pc = self.pop()
                pc_updated = True

            elif op == OP_OUT:
                self.output += chr(self.regs[rd] & 0xFF)

            elif op == OP_IN:
                if imm == 0 and self.port_valid and self.port_in is not None:
                    self.regs[rd] = self.port_in
                    self.port_valid = False
                else:
                    self.regs[rd] = -1

            elif op == OP_EI:
                self.intr_enabled = True

            elif op == OP_DI:
                self.intr_enabled = False

            elif op == OP_IRET:
                if not self.ctx_stack:
                    raise ProcessorError("IRET при пустом стеке контекстов")
                self.pc, self.flags, self.intr_enabled = self.ctx_stack.pop()
                pc_updated = True

            elif op == OP_POLY:
                n = instr & 0xFFFFF
                base = self.pc // WORD_BYTES
                operands = []
                for k in range(1 + 2 * n):
                    if base + 1 + k >= len(self.imem):
                        raise ProcessorError("POLY: операнды за границей IMEM")
                    operands.append(self.imem[base + 1 + k])
                result = s32(operands[0])
                for k in range(n):
                    result += s32(operands[1 + 2 * k]) * s32(operands[2 + 2 * k])
                self.regs[rd] = result & 0xFFFFFFFF
                self.pc += WORD_BYTES * (2 + 2 * n)
                pc_updated = True
                self.ticks += 1 + 4 * n

            else:
                raise ProcessorError(
                    f"неизвестный опкод 0x{op:02X} (PC=0x{self.pc:04X})"
                )

            if not pc_updated:
                self.pc += WORD_BYTES
            self.state = "WB"

        elif self.state == "WB":
            self.state = "FETCH1"
            self.dump_state()

        self.ticks += 1
        return True

    def run(self, limit: int = 10000000) -> str:
        try:
            while not self.halted and self.ticks < limit:
                if not self.step():
                    break
        except ProcessorError as e:
            self.error = str(e)
            self.halted = True
        return self.output


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python machine.py <binary> [input_file] "
            "[--schedule <file>] [--log <file>] [--limit N] "
            "[--pipeline | --no-pipeline]"
        )
        sys.exit(1)

    binary_path = sys.argv[1]
    input_path = None
    log_path = None
    schedule_path = None
    limit = 10000000
    model = "pipeline"

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--log" and i + 1 < len(args):
            log_path = args[i + 1]
            i += 2
        elif args[i] == "--schedule" and i + 1 < len(args):
            schedule_path = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--pipeline":
            model = "pipeline"
            i += 1
        elif args[i] == "--no-pipeline":
            model = "multicycle"
            i += 1
        else:
            input_path = args[i]
            i += 1

    try:
        entry, imem, dmem = load_binary(binary_path)
    except (OSError, ProcessorError) as e:
        print(f"Ошибка загрузки: {e}", file=sys.stderr)
        sys.exit(2)

    if model == "pipeline":
        from pipeline import PipelineProcessor

        proc: ProcessorModel = PipelineProcessor(entry, imem, dmem)
    else:
        proc = Processor(entry, imem, dmem)

    if schedule_path:
        with open(schedule_path, encoding="utf-8") as f:
            proc.schedule = parse_schedule(f.read())
    elif input_path:
        try:
            with open(input_path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            print(f"Входной файл не найден: {input_path}", file=sys.stderr)
            text = ""
        events = [(10 + 1000 * k, ord(ch)) for k, ch in enumerate(text)]
        events.append((10 + 1000 * len(text), -1))
        proc.schedule = events

    out = proc.run(limit)

    if log_path:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(proc.log))
        print(f"Лог сохранён в {log_path}", file=sys.stderr)

    print(out, end="")
    print(f"\n\nИтого тактов: {proc.ticks}", file=sys.stderr)
    print(
        f"Прерываний: {proc.intr_count}, переполнений входа: {proc.input_overrun}",
        file=sys.stderr,
    )
    print(f"Модель: {model} ", file=sys.stderr)

    if proc.error:
        print(f"ОШИБКА МОДЕЛИ: {proc.error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
