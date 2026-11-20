from typing import Any

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
    enc_i,
)
from machine import (
    DMEM_SIZE,
    STACK_TOP,
    ProcessorError,
    s32,
    trunc_div,
    trunc_mod,
)

USES_RD = {
    OP_ADD,
    OP_ADDI,
    OP_SUB,
    OP_SUBI,
    OP_MUL,
    OP_DIV,
    OP_MOD,
    OP_ADC,
    OP_SBC,
    OP_CMP,
    OP_CMPI,
    OP_AND,
    OP_OR,
    OP_XOR,
    OP_NOT,
    OP_ST,
    OP_ST_IND,
    OP_PUSH,
    OP_OUT,
}
USES_RS = {
    OP_ADD,
    OP_SUB,
    OP_MUL,
    OP_DIV,
    OP_MOD,
    OP_ADC,
    OP_SBC,
    OP_CMP,
    OP_AND,
    OP_OR,
    OP_XOR,
    OP_MOV,
    OP_ST_IND,
    OP_LD_IND,
}
WRITES_RD = {
    OP_LDI,
    OP_LD,
    OP_LD_IND,
    OP_ADD,
    OP_ADDI,
    OP_SUB,
    OP_SUBI,
    OP_MUL,
    OP_DIV,
    OP_MOD,
    OP_ADC,
    OP_SBC,
    OP_AND,
    OP_OR,
    OP_XOR,
    OP_NOT,
    OP_MOV,
    OP_POP,
    OP_IN,
    OP_POLY,
}
CONTROL_FLOW = {
    OP_JMP,
    OP_JZ,
    OP_JNZ,
    OP_JL,
    OP_JG,
    OP_JLE,
    OP_JGE,
    OP_CALL,
    OP_RET,
    OP_IRET,
    OP_HALT,
}
LOADS = {OP_LD, OP_LD_IND, OP_POP}


class PipelineProcessor:
    def __init__(self, entry: int, imem: list[int], dmem: list[int]) -> None:
        self.pc: int = entry
        self.imem: list[int] = list(imem) + [enc_i(OP_HALT, 0, 0)] * 4
        self.dmem: list[int] = dmem
        self.regs: list[int] = [0] * 4
        self.sp: int = STACK_TOP
        self.flags: dict[str, int] = {"Z": 0, "N": 0, "C": 0}
        self.output: str = ""
        self.halted: bool = False
        self.error: str | None = None
        self.ticks: int = 0
        self.log: list[str] = []

        self.if_id: dict[str, Any] | None = None
        self.id_ex: dict[str, Any] | None = None
        self.ex_mem: dict[str, Any] | None = None
        self.mem_wb: dict[str, Any] | None = None

        self.poly_fetch: dict[str, Any] | None = None
        self.poly_busy: int = 0

        self.intr_enabled: bool = False
        self.intr_pending: bool = False
        self.ctx_stack: list[tuple[int, dict[str, int], bool]] = []
        self.port_in: int | None = None
        self.port_valid: bool = False
        self.input_overrun: int = 0
        self.schedule: list[tuple[int, int]] = []
        self.schedule_pos: int = 0
        self.intr_count: int = 0

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

    @property
    def in_progress(self) -> bool:
        return bool(self.ctx_stack)

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

    def poly_in_flight(self) -> bool:
        if self.poly_fetch is not None or self.poly_busy > 0:
            return True
        for stage in (self.if_id, self.id_ex, self.ex_mem, self.mem_wb):
            if stage is not None and stage.get("op") == OP_POLY:
                return True
        return False

    def can_take_interrupt(self) -> bool:
        if not (self.intr_pending and self.intr_enabled):
            return False
        if self.poly_in_flight():
            return False
        return self.id_ex is None

    def take_interrupt(self) -> None:
        vector = self.imem[0] if self.imem else 0
        self.intr_pending = False
        if vector == 0:
            return
        saved_pc = self.if_id["pc"] if self.if_id else self.pc
        self.ctx_stack.append((saved_pc, dict(self.flags), self.intr_enabled))
        self.intr_enabled = False
        self.if_id = None
        self.pc = vector
        self.intr_count += 1
        self.log.append(
            f"tick={self.ticks:5d} | INTR accepted -> PC=0x{vector:04X} "
            f"depth={len(self.ctx_stack)} saved_pc=0x{saved_pc:04X}"
        )

    def stage_wb(self) -> None:
        wb = self.mem_wb
        self.mem_wb = None
        if wb is None or wb["wb"] is None:
            return
        self.regs[wb["wb"]] = wb["value"] & 0xFFFFFFFF

    def stage_mem(self) -> None:
        mem = self.ex_mem
        self.ex_mem = None
        if mem is None:
            return
        value = mem["value"]
        wb = mem["wb"]
        if mem["is_load"]:
            value = self.mem_read(mem["mem_addr"])
        elif mem["is_store"]:
            self.mem_write(mem["mem_addr"], mem["store_val"])
        self.mem_wb = {"pc": mem["pc"], "op": mem["op"], "wb": wb, "value": value}

    def forward(self, reg: int, fallback: int) -> int:
        em = self.ex_mem
        if em is not None and em["wb"] == reg and not em["is_load"]:
            return int(em["value"])
        wb = self.mem_wb
        if wb is not None and wb["wb"] == reg:
            return int(wb["value"])
        return fallback

    def stage_ex(self) -> bool:
        ex = self.id_ex
        self.id_ex = None
        if ex is None:
            return False
        op = ex["op"]
        rd = ex["rd"]
        va = self.forward(rd, self.regs[rd]) if op in USES_RD else 0
        vb = self.forward(ex["rs"], self.regs[ex["rs"]]) if op in USES_RS else 0
        imm = ex["imm"]

        out = {
            "pc": ex["pc"],
            "op": op,
            "wb": None,
            "value": 0,
            "store_val": 0,
            "mem_addr": 0,
            "is_load": False,
            "is_store": False,
        }

        if op == OP_HALT:
            self.halted = True
            return True

        if op == OP_NOP:
            pass

        elif op == OP_LDI:
            out["wb"] = rd
            out["value"] = imm

        elif op == OP_LD:
            out["wb"] = rd
            out["is_load"] = True
            out["mem_addr"] = ex["addr"]

        elif op == OP_LD_IND:
            out["wb"] = rd
            out["is_load"] = True
            out["mem_addr"] = vb

        elif op == OP_ST:
            out["is_store"] = True
            out["mem_addr"] = ex["addr"]
            out["store_val"] = va

        elif op == OP_ST_IND:
            out["is_store"] = True
            out["mem_addr"] = va
            out["store_val"] = vb

        elif op == OP_ADD:
            u = va + vb
            self.set_flags(u, u > 0xFFFFFFFF)
            out["wb"] = rd
            out["value"] = u & 0xFFFFFFFF

        elif op == OP_ADDI:
            u = va + (imm & 0xFFFFFFFF)
            self.set_flags(u, u > 0xFFFFFFFF)
            out["wb"] = rd
            out["value"] = u & 0xFFFFFFFF

        elif op == OP_SUB:
            u = va - vb
            self.set_flags(u, va < vb)
            out["wb"] = rd
            out["value"] = u & 0xFFFFFFFF

        elif op == OP_SUBI:
            u = va - (imm & 0xFFFFFFFF)
            self.set_flags(u, va < (imm & 0xFFFFFFFF))
            out["wb"] = rd
            out["value"] = u & 0xFFFFFFFF

        elif op == OP_MUL:
            r = s32(va) * s32(vb)
            self.set_flags(r, False)
            out["wb"] = rd
            out["value"] = r & 0xFFFFFFFF

        elif op == OP_DIV:
            out["wb"] = rd
            out["value"] = trunc_div(s32(va), s32(vb)) & 0xFFFFFFFF

        elif op == OP_MOD:
            out["wb"] = rd
            out["value"] = trunc_mod(s32(va), s32(vb)) & 0xFFFFFFFF

        elif op == OP_ADC:
            u = va + vb + self.flags["C"]
            self.set_flags(u, u > 0xFFFFFFFF)
            out["wb"] = rd
            out["value"] = u & 0xFFFFFFFF

        elif op == OP_SBC:
            sub = vb + self.flags["C"]
            u = va - sub
            self.set_flags(u, va < sub)
            out["wb"] = rd
            out["value"] = u & 0xFFFFFFFF

        elif op == OP_CMP:
            self.set_flags(va - vb, va < vb)

        elif op == OP_CMPI:
            self.set_flags(va - (imm & 0xFFFFFFFF), va < (imm & 0xFFFFFFFF))

        elif op == OP_AND:
            out["wb"] = rd
            out["value"] = (va & vb) & 0xFFFFFFFF

        elif op == OP_OR:
            out["wb"] = rd
            out["value"] = (va | vb) & 0xFFFFFFFF

        elif op == OP_XOR:
            out["wb"] = rd
            out["value"] = (va ^ vb) & 0xFFFFFFFF

        elif op == OP_NOT:
            out["wb"] = rd
            out["value"] = (~va) & 0xFFFFFFFF

        elif op == OP_MOV:
            out["wb"] = rd
            out["value"] = vb

        elif op == OP_PUSH:
            self.sp -= WORD_BYTES
            out["is_store"] = True
            out["mem_addr"] = self.sp
            out["store_val"] = va

        elif op == OP_POP:
            out["wb"] = rd
            out["is_load"] = True
            out["mem_addr"] = self.sp
            self.sp += WORD_BYTES

        elif op == OP_JMP:
            self.redirect(ex["jaddr"])

        elif op == OP_JZ:
            if self.flags["Z"]:
                self.redirect(ex["jaddr"])

        elif op == OP_JNZ:
            if not self.flags["Z"]:
                self.redirect(ex["jaddr"])

        elif op == OP_JL:
            if self.flags["N"]:
                self.redirect(ex["jaddr"])

        elif op == OP_JG:
            if not self.flags["N"] and not self.flags["Z"]:
                self.redirect(ex["jaddr"])

        elif op == OP_JLE:
            if self.flags["N"] or self.flags["Z"]:
                self.redirect(ex["jaddr"])

        elif op == OP_JGE:
            if not self.flags["N"]:
                self.redirect(ex["jaddr"])

        elif op == OP_CALL:
            self.push(ex["pc"] + WORD_BYTES)
            self.redirect(ex["jaddr"])

        elif op == OP_RET:
            self.redirect(self.pop())

        elif op == OP_OUT:
            self.output += chr(va & 0xFF)

        elif op == OP_IN:
            out["wb"] = rd
            if imm == 0 and self.port_valid:
                out["value"] = self.port_in
                self.port_valid = False
            else:
                out["value"] = -1

        elif op == OP_EI:
            self.intr_enabled = True

        elif op == OP_DI:
            self.intr_enabled = False

        elif op == OP_IRET:
            if not self.ctx_stack:
                raise ProcessorError("IRET при пустом стеке контекстов")
            self.pc, self.flags, self.intr_enabled = self.ctx_stack.pop()
            self.if_id = None

        elif op == OP_POLY:
            n = len(ex["poly"]) // 2
            words = ex["poly"]
            result = s32(words[0])
            for k in range(n):
                result += s32(words[1 + 2 * k]) * s32(words[2 + 2 * k])
            out["wb"] = rd
            out["value"] = result & 0xFFFFFFFF
            self.poly_busy = 2 * n

        else:
            raise ProcessorError(f"неизвестный опкод 0x{op:02X} (PC=0x{ex['pc']:04X})")

        self.ex_mem = out
        return False

    def redirect(self, target: int) -> None:
        self.pc = target & 0xFFFFFF
        self.if_id = None

    def stage_id(self) -> None:
        if self.if_id is None:
            self.id_ex = None
            return
        instr = self.if_id["instr"]
        pc = self.if_id["pc"]
        poly = self.if_id.get("poly")
        self.if_id = None
        op = (instr >> 24) & 0xFF
        rd = (instr >> 20) & 0xF
        rs = (instr >> 16) & 0xF
        imm = instr & 0xFFFFF
        if imm & (1 << 19):
            imm -= 1 << 20
        self.id_ex = {
            "pc": pc,
            "instr": instr,
            "op": op,
            "rd": rd,
            "rs": rs,
            "imm": imm,
            "addr": instr & 0xFFFFF,
            "jaddr": instr & 0xFFFFFF,
            "poly": poly,
        }

    def stage_if(self) -> None:
        if self.poly_fetch is not None:
            pf = self.poly_fetch
            if self.pc + WORD_BYTES > len(self.imem) * WORD_BYTES:
                raise ProcessorError("POLY: операнды за границей IMEM")
            pf["words"].append(self.imem[self.pc // WORD_BYTES])
            self.pc += WORD_BYTES
            if len(pf["words"]) == pf["need"]:
                self.if_id = {
                    "pc": pf["pc"],
                    "instr": self.imem[pf["pc"] // WORD_BYTES],
                    "poly": pf["words"],
                    "op": OP_POLY,
                }
                self.poly_fetch = None
            return

        if self.pc % WORD_BYTES:
            raise ProcessorError(f"невыровненный PC: 0x{self.pc:04X}")
        if self.pc < 0 or self.pc + WORD_BYTES > len(self.imem) * WORD_BYTES:
            raise ProcessorError(f"PC вне IMEM: 0x{self.pc:04X}")

        instr = self.imem[self.pc // WORD_BYTES]
        op = (instr >> 24) & 0xFF
        if op == OP_POLY:
            n = instr & 0xFFFFF
            self.poly_fetch = {"pc": self.pc, "need": 2 * n + 1, "words": []}
            self.pc += WORD_BYTES
            return
        self.if_id = {"pc": self.pc, "instr": instr, "op": op}
        self.pc += WORD_BYTES

    def stall_needed(self) -> bool:
        em = self.ex_mem
        if em is None or not em["is_load"] or self.if_id is None:
            return False
        dest = em["wb"]
        instr = self.if_id["instr"]
        op = (instr >> 24) & 0xFF
        rd = (instr >> 20) & 0xF
        rs = (instr >> 16) & 0xF
        if op in USES_RD and rd == dest:
            return True
        return bool(op in USES_RS and rs == dest)

    def set_flags(self, result: int, carry: bool) -> None:
        self.flags["Z"] = 1 if (result & 0xFFFFFFFF) == 0 else 0
        self.flags["N"] = 1 if (result & 0x80000000) else 0
        self.flags["C"] = 1 if carry else 0

    def stage_names(self) -> str:
        def pc_of(stage: dict[str, Any] | None) -> str:
            return f"{stage['pc']:04X}" if stage else "----"

        return (
            f"IF={pc_of(self.if_id)} ID={pc_of(self.id_ex)} "
            f"EX={pc_of(self.ex_mem)} WB={pc_of(self.mem_wb)}"
        )

    def dump_state(self) -> None:
        regs_str = " ".join(f"R{i}={self.regs[i]}" for i in range(4))
        flags_str = f"Z={self.flags['Z']} N={self.flags['N']} C={self.flags['C']}"
        intr_str = (
            f"INTR={'on' if self.in_progress else 'off'}"
            f" pend={1 if self.intr_pending else 0}"
        )
        self.log.append(
            f"tick={self.ticks:5d} | PC=0x{self.pc:04X} | "
            f"{self.stage_names()} | {regs_str} | {flags_str} | {intr_str}"
        )

    def step(self) -> bool:
        if self.halted:
            return False
        self.ticks += 1
        self.check_schedule()

        taken = self.can_take_interrupt()
        if taken:
            self.take_interrupt()

        self.stage_wb()
        self.stage_mem()
        halted_in_ex = self.stage_ex()

        if halted_in_ex:
            self.dump_state()
            return False

        busy = taken or self.poly_busy > 0 or self.stall_needed()
        if busy:
            self.id_ex = None
            if self.poly_busy > 0:
                self.poly_busy -= 1
        else:
            self.stage_id()
            self.stage_if()

        self.dump_state()
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
