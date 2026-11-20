WORD_BYTES = 4

OP_NOP = 0x00
OP_HALT = 0x01

OP_LDI = 0x10
OP_LD = 0x11
OP_LD_IND = 0x12
OP_ST = 0x13
OP_ST_IND = 0x14
OP_PUSH = 0x15
OP_POP = 0x16
OP_MOV = 0x17

OP_ADD = 0x20
OP_ADDI = 0x39
OP_SUB = 0x23
OP_SUBI = 0x3A
OP_MUL = 0x26
OP_DIV = 0x28
OP_MOD = 0x2A
OP_CMP = 0x2F
OP_CMPI = 0x38
OP_ADC = 0x3B
OP_SBC = 0x3C
OP_AND = 0x31
OP_OR = 0x33
OP_XOR = 0x35
OP_NOT = 0x37

OP_JMP = 0x40
OP_JZ = 0x41
OP_JNZ = 0x42
OP_JL = 0x43
OP_JG = 0x44
OP_JLE = 0x45
OP_JGE = 0x46
OP_CALL = 0x47
OP_RET = 0x48

OP_IN = 0x50
OP_OUT = 0x51

OP_EI = 0x60
OP_DI = 0x61
OP_IRET = 0x62

OP_POLY = 0x70


def enc_r(op: int, rd: int, rs: int) -> int:
    return (op << 24) | ((rd & 0xF) << 20) | ((rs & 0xF) << 16)


def enc_i(op: int, rd: int, imm: int) -> int:
    return (op << 24) | ((rd & 0xF) << 20) | (imm & 0xFFFFF)


def enc_m(op: int, rd: int, addr: int) -> int:
    return (op << 24) | ((rd & 0xF) << 20) | (addr & 0xFFFFF)


def enc_j(op: int, addr: int) -> int:
    return (op << 24) | (addr & 0xFFFFFF)
