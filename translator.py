import os
import re
import struct
import sys
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
    enc_j,
    enc_m,
    enc_r,
)

MNEMONICS = {
    OP_NOP: "NOP",
    OP_HALT: "HALT",
    OP_LDI: "LDI",
    OP_LD: "LD",
    OP_LD_IND: "LD_IND",
    OP_ST: "ST",
    OP_ST_IND: "ST_IND",
    OP_ADD: "ADD",
    OP_SUB: "SUB",
    OP_MUL: "MUL",
    OP_DIV: "DIV",
    OP_MOD: "MOD",
    OP_ADDI: "ADDI",
    OP_SUBI: "SUBI",
    OP_CMP: "CMP",
    OP_CMPI: "CMPI",
    OP_ADC: "ADC",
    OP_SBC: "SBC",
    OP_MOV: "MOV",
    OP_PUSH: "PUSH",
    OP_POP: "POP",
    OP_JMP: "JMP",
    OP_JZ: "JZ",
    OP_JNZ: "JNZ",
    OP_JL: "JL",
    OP_JG: "JG",
    OP_JLE: "JLE",
    OP_JGE: "JGE",
    OP_CALL: "CALL",
    OP_RET: "RET",
    OP_OUT: "OUT",
    OP_IN: "IN",
    OP_AND: "AND",
    OP_OR: "OR",
    OP_XOR: "XOR",
    OP_NOT: "NOT",
    OP_POLY: "POLY",
    OP_EI: "EI",
    OP_DI: "DI",
    OP_IRET: "IRET",
}

VECTOR_WORDS = 2

IMM_MIN = -0x80000
IMM_MAX = 0x7FFFF

STDLIB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lib", "stdlib.lisp"
)


class StrLit(str):
    pass


def tokenize(source: str) -> list[str]:
    source = re.sub(r";.*", "", source)
    pattern = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+')
    return pattern.findall(source)


def unescape(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append(
                {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt)
            )
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def parse(tokens: list[str]) -> Any:
    if not tokens:
        raise SyntaxError("Неожиданный конец")
    tok = tokens.pop(0)
    if tok == "(":
        lst: list[Any] = []
        while tokens and tokens[0] != ")":
            lst.append(parse(tokens))
        if not tokens:
            raise SyntaxError("Не закрыта скобка")
        tokens.pop(0)
        return lst
    if tok == ")":
        raise SyntaxError("Лишняя закрывающая скобка")
    if tok.startswith('"'):
        return StrLit(unescape(tok[1:-1]))
    try:
        return int(tok)
    except ValueError:
        return tok


def parse_program(source: str) -> list[Any]:
    tokens = tokenize(source)
    ast: list[Any] = []
    while tokens:
        ast.append(parse(tokens))
    return ast


def ast_to_string(ast: Any) -> str:
    if isinstance(ast, StrLit):
        return (
            '"'
            + (ast.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n"))
            + '"'
        )
    if isinstance(ast, list):
        return "(" + " ".join(ast_to_string(x) for x in ast) + ")"
    if isinstance(ast, str):
        return ast
    return str(ast)


def is_defun(expr: Any) -> bool:
    return isinstance(expr, list) and bool(expr) and expr[0] == "defun"


def merge_programs(base: list[Any], user: list[Any]) -> list[Any]:
    user_defs = {e[1] for e in user if is_defun(e)}
    merged = [e for e in base if not (is_defun(e) and e[1] in user_defs)]
    return merged + user


class CodeGen:
    def __init__(self) -> None:
        self.code: list[int] = [0] * VECTOR_WORDS
        self.data: list[int] = []
        self.vars: dict[str, int] = {}
        self.strings: dict[str, int] = {}
        self.literals: dict[int, int] = {}
        self.labels: dict[str, int] = {}
        self.funcs: dict[str, tuple[list[str], list[Any]]] = {}
        self.pending_calls: list[tuple[int, str]] = []
        self.vectors: dict[Any, str] = {}
        self.handlers: set[str] = set()
        self.param_slots: dict[str, int] = {}
        self.data_base: int = 0x0100
        self.entry: int = VECTOR_WORDS * WORD_BYTES

    def alloc(self, n: int = 1) -> int:
        addr = self.data_base + len(self.data) * WORD_BYTES
        self.data.extend([0] * n)
        return addr

    def add_string(self, s: str) -> int:
        if s not in self.strings:
            addr = self.data_base + len(self.data) * WORD_BYTES
            self.strings[s] = addr
            for ch in s:
                self.data.append(ord(ch) & 0xFFFFFFFF)
            self.data.append(0)
        return self.strings[s]

    def add_var(self, name: str) -> int:
        if name not in self.vars:
            self.vars[name] = self.alloc(1)
        return self.vars[name]

    def add_literal(self, value: int) -> int:
        if value not in self.literals:
            self.literals[value] = self.alloc(1)
            idx = (self.literals[value] - self.data_base) // WORD_BYTES
            self.data[idx] = value & 0xFFFFFFFF
        return self.literals[value]

    def lookup(self, name: str) -> int:
        if name in self.param_slots:
            return self.param_slots[name]
        return self.add_var(name)

    def emit(self, word: int) -> int:
        self.code.append(word)
        return len(self.code) - 1

    def here(self) -> int:
        return len(self.code) * WORD_BYTES

    def patch(self, idx: int, target: int) -> None:
        op = self.code[idx] & 0xFF000000
        self.code[idx] = op | (target & 0xFFFFFF)

    def emit_addr(self, target: int, addr: int) -> None:
        if IMM_MIN <= addr <= IMM_MAX:
            self.emit(enc_i(OP_LDI, target, addr))
        else:
            self.emit(enc_m(OP_LD, target, self.add_literal(addr)))

    def compile(self, ast: list[Any]) -> tuple[list[int], list[int], int]:
        for expr in ast:
            if is_defun(expr):
                name = expr[1]
                params = expr[2] if len(expr) > 2 else []
                body = expr[3:]
                if name in self.funcs:
                    raise SyntaxError(f"Функция {name} определена дважды")
                self.funcs[name] = (params, body)
            elif (
                isinstance(expr, list)
                and len(expr) == 3
                and expr[0] == "interrupt-handler"
            ):
                self.handlers.add(expr[1])
                self.vectors[expr[2]] = expr[1]

        for expr in ast:
            if is_defun(expr):
                name = expr[1]
                params, body = self.funcs[name]
                self.labels[name] = self.here()
                self.compile_defun(name, params, body)

        self.entry = self.here()

        for expr in ast:
            if is_defun(expr):
                continue
            self.compile_expr(expr, 0)

        self.emit(enc_i(OP_HALT, 0, 0))

        for idx, name in self.pending_calls:
            if name not in self.labels:
                raise SyntaxError(f"Вызов неизвестной функции: {name}")
            self.patch(idx, self.labels[name])

        for vec, name in self.vectors.items():
            if not isinstance(vec, int) or not (0 <= vec < VECTOR_WORDS):
                raise SyntaxError(f"Некорректный вектор прерывания: {vec}")
            if name not in self.labels:
                raise SyntaxError(f"Неизвестный обработчик прерывания: {name}")
            self.code[vec] = self.labels[name]

        return self.code, self.data, self.entry

    def compile_defun(self, name: str, params: list[str], body: list[Any]) -> None:
        saved = self.param_slots
        self.param_slots = {p: self.add_var(f"{name}${p}") for p in params}
        if name in self.handlers:
            for r in range(4):
                self.emit(enc_r(OP_PUSH, r, 0))
        for stmt in body:
            self.compile_expr(stmt, 0)
        if not body:
            self.emit(enc_i(OP_LDI, 0, 0))
        if name in self.handlers:
            for r in reversed(range(4)):
                self.emit(enc_r(OP_POP, r, 0))
            self.emit(enc_i(OP_IRET, 0, 0))
        else:
            self.emit(enc_j(OP_RET, 0))
        self.param_slots = saved

    def emit_call(self, name: str, args: list[Any]) -> None:
        params, _ = self.funcs[name]
        if len(params) > 4:
            raise SyntaxError(f"{name}: не более 4 параметров (соглашение о вызовах)")
        n = min(len(args), len(params))

        for p in params[:n]:
            self.emit(enc_m(OP_LD, 3, self.vars[f"{name}${p}"]))
            self.emit(enc_r(OP_PUSH, 3, 0))

        for a in args[:n]:
            self.compile_expr(a, 0)
            self.emit(enc_r(OP_PUSH, 0, 0))

        for i in reversed(range(n)):
            self.emit(enc_r(OP_POP, i, 0))

        for i, p in enumerate(params[:n]):
            self.emit(enc_m(OP_ST, i, self.vars[f"{name}${p}"]))

        idx = self.emit(enc_j(OP_CALL, 0))
        self.pending_calls.append((idx, name))

        for p in reversed(params[:n]):
            self.emit(enc_r(OP_POP, 3, 0))
            self.emit(enc_m(OP_ST, 3, self.vars[f"{name}${p}"]))

    def compile_expr(self, expr: Any, target: int = 0) -> None:
        if isinstance(expr, StrLit):
            self.emit_addr(target, self.add_string(expr))
            return

        if isinstance(expr, bool):
            self.emit(enc_i(OP_LDI, target, 1 if expr else 0))
            return

        if isinstance(expr, int):
            if IMM_MIN <= expr <= IMM_MAX:
                self.emit(enc_i(OP_LDI, target, expr))
            else:
                self.emit(enc_m(OP_LD, target, self.add_literal(expr)))
            return

        if isinstance(expr, str):
            self.emit(enc_m(OP_LD, target, self.lookup(expr)))
            return

        if not isinstance(expr, list) or not expr:
            self.emit(enc_i(OP_LDI, target, 0))
            return

        op = expr[0]

        if op == "defun":
            return

        if op == "interrupt-handler":
            return

        if op == "ei":
            self.emit(enc_i(OP_EI, 0, 0))
            return

        if op == "di":
            self.emit(enc_i(OP_DI, 0, 0))
            return

        if op == "iret":
            self.emit(enc_i(OP_IRET, 0, 0))
            return

        if op == "setq":
            name = expr[1]
            self.compile_expr(expr[2], 0)
            self.emit(enc_m(OP_ST, 0, self.lookup(name)))
            return

        if op == "if":
            cond = expr[1]
            then = expr[2]
            else_ = expr[3] if len(expr) > 3 else 0
            self.compile_expr(cond, 0)
            self.emit(enc_i(OP_CMPI, 0, 0))
            jz_else = self.emit(enc_j(OP_JZ, 0))
            self.compile_expr(then, 0)
            jmp_end = self.emit(enc_j(OP_JMP, 0))
            self.patch(jz_else, self.here())
            self.compile_expr(else_, 0)
            self.patch(jmp_end, self.here())
            return

        if op == "do":
            if len(expr) == 1:
                self.emit(enc_i(OP_LDI, 0, 0))
            for e in expr[1:]:
                self.compile_expr(e, 0)
            return

        if op == "while":
            cond = expr[1]
            body = expr[2:]
            loop_start = self.here()
            self.compile_expr(cond, 0)
            self.emit(enc_i(OP_CMPI, 0, 0))
            jz_end = self.emit(enc_j(OP_JZ, 0))
            for stmt in body:
                self.compile_expr(stmt, 0)
            self.emit(enc_j(OP_JMP, loop_start))
            self.patch(jz_end, self.here())
            self.emit(enc_i(OP_LDI, 0, 0))
            return

        if op == "and":
            if len(expr) == 1:
                self.emit(enc_i(OP_LDI, 0, 1))
                return
            end_jumps = []
            for i in range(1, len(expr)):
                self.compile_expr(expr[i], 0)
                if i < len(expr) - 1:
                    self.emit(enc_i(OP_CMPI, 0, 0))
                    end_jumps.append(self.emit(enc_j(OP_JZ, 0)))
            for j in end_jumps:
                self.patch(j, self.here())
            return

        if op == "or":
            if len(expr) == 1:
                self.emit(enc_i(OP_LDI, 0, 0))
                return
            end_jumps = []
            for i in range(1, len(expr)):
                self.compile_expr(expr[i], 0)
                if i < len(expr) - 1:
                    self.emit(enc_i(OP_CMPI, 0, 0))
                    end_jumps.append(self.emit(enc_j(OP_JNZ, 0)))
            for j in end_jumps:
                self.patch(j, self.here())
            return

        if op == "not":
            self.compile_expr(expr[1], 0)
            self.emit(enc_i(OP_CMPI, 0, 0))
            self.emit(enc_i(OP_LDI, 1, 1))
            jz_true = self.emit(enc_j(OP_JZ, 0))
            self.emit(enc_i(OP_LDI, 1, 0))
            self.patch(jz_true, self.here())
            self.emit(enc_r(OP_MOV, 0, 1))
            return

        if op == "in":
            port = expr[1] if len(expr) > 1 else 0
            self.emit(enc_i(OP_IN, 0, port if isinstance(port, int) else 0))
            return

        if op == "out":
            self.compile_expr(expr[2], 0)
            self.emit(enc_r(OP_OUT, 0, 0))
            return

        if op == "mem-get":
            self.compile_expr(expr[1], 0)
            self.emit(enc_r(OP_LD_IND, 0, 0))
            return

        if op == "mem-set":
            self.compile_expr(expr[1], 0)
            self.emit(enc_r(OP_PUSH, 0, 0))
            self.compile_expr(expr[2], 0)
            self.emit(enc_r(OP_MOV, 1, 0))
            self.emit(enc_r(OP_POP, 0, 0))
            self.emit(enc_r(OP_ST_IND, 0, 1))
            return

        if op == "buf":
            n = expr[1]
            if not isinstance(n, int) or n < 0:
                raise SyntaxError("buf: размер должен быть неотрицательным целым")
            self.emit_addr(0, self.alloc(n))
            return

        if op == "poly":
            self.emit_poly(expr)
            return

        if op in self.funcs:
            self.emit_call(op, expr[1:])
            return

        bin_ops = {
            "+": OP_ADD,
            "-": OP_SUB,
            "*": OP_MUL,
            "/": OP_DIV,
            "%": OP_MOD,
            "adc": OP_ADC,
            "sbc": OP_SBC,
        }
        if op in bin_ops:
            if len(expr) < 3:
                raise SyntaxError(f"{op}: нужно минимум 2 аргумента")
            self.compile_expr(expr[1], 0)
            for arg in expr[2:]:
                self.emit(enc_r(OP_PUSH, 0, 0))
                self.compile_expr(arg, 0)
                self.emit(enc_r(OP_MOV, 1, 0))
                self.emit(enc_r(OP_POP, 0, 0))
                self.emit(enc_r(bin_ops[op], 0, 1))
            return

        def cmp_setup() -> None:
            self.compile_expr(expr[1], 0)
            self.emit(enc_r(OP_PUSH, 0, 0))
            self.compile_expr(expr[2], 0)
            self.emit(enc_r(OP_MOV, 1, 0))
            self.emit(enc_r(OP_POP, 0, 0))
            self.emit(enc_r(OP_CMP, 0, 1))

        def cmp_finish(jump_op: int, true_on_jump: bool) -> None:
            self.emit(enc_i(OP_LDI, 1, 1 if true_on_jump else 0))
            j = self.emit(enc_j(jump_op, 0))
            self.emit(enc_i(OP_LDI, 1, 0 if true_on_jump else 1))
            self.patch(j, self.here())
            self.emit(enc_r(OP_MOV, 0, 1))

        if op == "=":
            cmp_setup()
            cmp_finish(OP_JZ, True)
            return

        if op == "/=":
            cmp_setup()
            cmp_finish(OP_JNZ, True)
            return

        if op == "<":
            cmp_setup()
            cmp_finish(OP_JL, True)
            return

        if op == ">":
            self.compile_expr(expr[1], 0)
            self.emit(enc_r(OP_PUSH, 0, 0))
            self.compile_expr(expr[2], 0)
            self.emit(enc_r(OP_MOV, 1, 0))
            self.emit(enc_r(OP_POP, 0, 0))
            self.emit(enc_r(OP_CMP, 0, 1))
            self.emit(enc_i(OP_LDI, 1, 0))
            jz = self.emit(enc_j(OP_JZ, 0))
            jl = self.emit(enc_j(OP_JL, 0))
            self.emit(enc_i(OP_LDI, 1, 1))
            self.patch(jz, self.here())
            self.patch(jl, self.here())
            self.emit(enc_r(OP_MOV, 0, 1))
            return

        if op == "<=":
            cmp_setup()
            self.emit(enc_i(OP_LDI, 1, 1))
            jl = self.emit(enc_j(OP_JL, 0))
            jz = self.emit(enc_j(OP_JZ, 0))
            self.emit(enc_i(OP_LDI, 1, 0))
            self.patch(jl, self.here())
            self.patch(jz, self.here())
            self.emit(enc_r(OP_MOV, 0, 1))
            return

        if op == ">=":
            cmp_setup()
            self.emit(enc_i(OP_LDI, 1, 0))
            jl = self.emit(enc_j(OP_JL, 0))
            self.emit(enc_i(OP_LDI, 1, 1))
            self.patch(jl, self.here())
            self.emit(enc_r(OP_MOV, 0, 1))
            return

        raise SyntaxError(f"Неизвестная форма: {op}")

    def emit_poly(self, expr: list[Any]) -> None:
        if len(expr) < 2:
            raise SyntaxError("poly: нужен хотя бы свободный член")
        c0 = expr[1]
        pairs = expr[2:]
        if not isinstance(c0, int):
            raise SyntaxError("poly: коэффициенты должны быть литералами")
        for p in pairs:
            if (
                not isinstance(p, list)
                or len(p) != 2
                or not isinstance(p[0], int)
                or not isinstance(p[1], int)
            ):
                raise SyntaxError("poly: ожидается (ci xi) из литералов")
        word = enc_i(OP_POLY, 0, len(pairs))
        self.emit(word)
        self.emit(c0 & 0xFFFFFFFF)
        for c, x in pairs:
            self.emit(c & 0xFFFFFFFF)
            self.emit(x & 0xFFFFFFFF)


def write_listing(code: list[int], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        addr = 0
        while addr < len(code) * WORD_BYTES:
            instr = code[addr // WORD_BYTES]
            op = (instr >> 24) & 0xFF
            mnem = MNEMONICS.get(op, f"UNKNOWN_{op:02X}")
            f.write(f"0x{addr:04X} - 0x{instr:08X} - {mnem}\n")
            addr += WORD_BYTES
            if op == OP_POLY:
                n = instr & 0xFFFFF
                for _ in range(1 + 2 * n):
                    if addr >= len(code) * WORD_BYTES:
                        break
                    f.write(
                        f"0x{addr:04X} - 0x{code[addr // WORD_BYTES]:08X} - .word\n"
                    )
                    addr += WORD_BYTES


def write_ast(ast: list[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for expr in ast:
            f.write(ast_to_string(expr) + "\n")


def load_program(path: str) -> list[Any]:
    with open(path, encoding="utf-8") as f:
        return parse_program(f.read())


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python translator.py <source.lisp> <target.bin>")
        sys.exit(1)

    ast = load_program(sys.argv[1])
    ast_path = sys.argv[2].replace(".bin", ".ast")
    write_ast(ast, ast_path)
    print(f"AST сохранён в {ast_path}")

    program = ast
    if os.path.exists(STDLIB_PATH):
        program = merge_programs(load_program(STDLIB_PATH), ast)

    gen = CodeGen()
    code, data, entry = gen.compile(program)

    binary = b""
    binary += struct.pack(">I", entry)
    binary += struct.pack(">I", len(data))
    for instr in code:
        binary += struct.pack(">I", instr)
    for d in data:
        binary += struct.pack(">I", d)
    with open(sys.argv[2], "wb") as f:
        f.write(binary)

    lst_path = sys.argv[2].replace(".bin", ".lst")
    write_listing(code, lst_path)
    print(f"Листинг сохранён в {lst_path}")

    print(f"OK: {len(code)} инструкций, {len(data)} слов данных.")


if __name__ == "__main__":
    main()
