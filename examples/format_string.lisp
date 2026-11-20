(defun format-string (s out)
  (do
    (setq i 0)
    (setq j 0)
    (setq err 0)
    (setq c (char-at s i))
    (while (and (/= c -1) (= err 0))
      (do
        (if (= c 37)
            (do
              (setq k (+ i 1))
              (setq left 0)
              (if (= (char-at s k) 45)
                  (do
                    (setq left 1)
                    (setq k (+ k 1))))
              (setq w (scan-width s k))
              (setq k __spec_end)
              (if (= (char-at s k) 100)
                  (do
                    (setq v (read-int-line))
                    (if (= __in_err 1)
                        (setq err 1)
                        (setq j (write-padded v out j
                                              (if (= left 1)
                                                  (- 0 w)
                                                  w))))
                    (setq i (+ k 1)))
                  (do
                    (char-set out j c)
                    (setq j (+ j 1))
                    (setq i (+ i 1)))))
            (do
              (char-set out j c)
              (setq j (+ j 1))
              (setq i (+ i 1))))
        (setq c (char-at s i))))
    (if (= err 1)
        -1
        (do
          (char-set out j 0)
          j))))

(setq fmt (buf 33))
(setq out (buf 1024))
(setq flen (read-line-limited fmt 33))
(if (= flen -1)
    (print-str "-1")
    (do
      (setq n (format-string fmt out))
      (if (= n -1)
          (print-str "-1")
          (print-str out))))
(print-char 10)
