(defun char-at (s i)
  (do
    (setq __ch (mem-get (+ s (* i 4))))
    (if (= __ch 0) -1 __ch)))

(defun char-set (s i c)
  (mem-set (+ s (* i 4)) c))

(defun str-len (s)
  (do
    (setq __i 0)
    (while (/= (char-at s __i) -1)
      (setq __i (+ __i 1)))
    __i))

(defun print-char (c)
  (out 1 c))

(defun print-str (s)
  (do
    (setq __i 0)
    (setq __c (char-at s __i))
    (while (/= __c -1)
      (do
        (out 1 __c)
        (setq __i (+ __i 1))
        (setq __c (char-at s __i))))))

(defun print-int (n)
  (if (= n -2147483648)
      (print-str "-2147483648")
      (do
        (if (< n 0)
            (do (out 1 45) (setq n (- 0 n))))
        (if (>= n 10) (print-int (/ n 10)))
        (out 1 (+ 48 (% n 10))))))

(setq __in_buf (buf 256))
(setq __in_head 0)
(setq __in_tail 0)
(setq __in_count 0)
(setq __in_eof 0)
(setq __in_overrun 0)
(setq __getc_result -1)

(defun __on-input ()
  (do
    (setq __isr_ch (in 0))
    (if (= __isr_ch -1)
        (setq __in_eof 1)
        (if (< __in_count 256)
            (do
              (mem-set (+ __in_buf (* __in_head 4)) __isr_ch)
              (setq __in_head (% (+ __in_head 1) 256))
              (setq __in_count (+ __in_count 1)))
            (setq __in_overrun (+ __in_overrun 1))))))

(interrupt-handler __on-input 0)

(defun getc ()
  (do
    (while (and (= __in_count 0) (= __in_eof 0))
      (do))
    (di)
    (if (= __in_count 0)
        (setq __getc_result -1)
        (do
          (setq __getc_result (char-at __in_buf __in_tail))
          (setq __in_tail (% (+ __in_tail 1) 256))
          (setq __in_count (- __in_count 1))))
    (ei)
    __getc_result))

(defun read-char ()
  (getc))

(defun read-line-limited (buf max)
  (do
    (setq __len 0)
    (setq __ovf 0)
    (setq __c (getc))
    (while (and (/= __c -1) (/= __c 10))
      (do
        (if (< __len (- max 1))
            (do
              (char-set buf __len __c)
              (setq __len (+ __len 1)))
            (setq __ovf 1))
        (setq __c (getc))))
    (if (= __ovf 1)
        -1
        (do
          (char-set buf __len 0)
          __len))))

(defun read-int-line ()
  (do
    (setq __n 0)
    (setq __sign 1)
    (setq __in_err 0)
    (setq __c (getc))
    (while (or (= __c 32) (= __c 9))
      (setq __c (getc)))
    (if (= __c 45)
        (do (setq __sign -1) (setq __c (getc)))
        (if (= __c 43) (setq __c (getc))))
    (if (and (>= __c 48) (<= __c 57))
        (do
          (while (and (>= __c 48) (<= __c 57))
            (do
              (setq __d (- __c 48))
              (if (> __n 214748364)
                  (setq __in_err 1)
                  (if (and (= __n 214748364)
                           (> __d (- 7 __sign)))
                      (setq __in_err 1)))
              (setq __n (+ (* __n 10) __d))
              (setq __c (getc))))
          (if (= __c 13) (setq __c (getc)))
          (if (and (/= __c -1) (/= __c 10))
              (do
                (while (and (/= __c -1) (/= __c 10))
                  (setq __c (getc)))
                (setq __in_err 1))))
        (do
          (while (and (/= __c -1) (/= __c 10))
            (setq __c (getc)))
          (setq __in_err 1)))
    (* __sign __n)))

(defun scan-width (s i)
  (do
    (setq __w 0)
    (setq __sc (char-at s i))
    (while (and (>= __sc 48) (<= __sc 57))
      (do
        (setq __w (+ (* __w 10) (- __sc 48)))
        (setq i (+ i 1))
        (setq __sc (char-at s i))))
    (setq __spec_end i)
    __w))

(defun int-len (n)
  (do
    (if (< n 0) (setq n (- 0 n)))
    (if (= n -2147483648)
        (setq __k 10)
        (do
          (setq __k 1)
          (while (>= n 10)
            (do
              (setq n (/ n 10))
              (setq __k (+ __k 1))))))
    __k))

(defun write-int (n out pos)
  (if (= n -2147483648)
      (do
        (char-set out pos 45)
        (setq pos (write-int 214748364 out (+ pos 1)))
        (char-set out pos 56)
        (+ pos 1))
      (if (< n 0)
          (do
            (char-set out pos 45)
            (write-int (- 0 n) out (+ pos 1)))
          (if (< n 10)
              (do
                (char-set out pos (+ 48 n))
                (+ pos 1))
              (do
                (setq pos (write-int (/ n 10) out pos))
                (char-set out pos (+ 48 (% n 10)))
                (+ pos 1))))))

(defun write-padded (n out pos spec)
  (do
    (setq __width spec)
    (setq __left 0)
    (if (< spec 0)
        (do
          (setq __left 1)
          (setq __width (- 0 spec))))
    (setq __len (int-len n))
    (if (< n 0) (setq __len (+ __len 1)))
    (setq __pad (- __width __len))
    (if (< __pad 0) (setq __pad 0))
    (if (= __left 0)
        (do
          (setq __i 0)
          (while (< __i __pad)
            (do
              (char-set out pos 32)
              (setq pos (+ pos 1))
              (setq __i (+ __i 1))))))
    (setq pos (write-int n out pos))
    (if (= __left 1)
        (do
          (setq __i 0)
          (while (< __i __pad)
            (do
              (char-set out pos 32)
              (setq pos (+ pos 1))
              (setq __i (+ __i 1))))))
    pos))

(ei)
