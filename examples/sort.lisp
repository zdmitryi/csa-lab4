(setq n (read-int-line))
(setq data (buf 64))

(setq i 0)
(while (< i n)
  (do
    (mem-set (+ data (* i 4)) (read-int-line))
    (setq i (+ i 1))))

(setq i 0)
(while (< i n)
  (do
    (setq j 0)
    (while (< j (- n 1))
      (do
        (if (> (mem-get (+ data (* j 4))) (mem-get (+ data (* (+ j 1) 4))))
            (do
              (setq t (mem-get (+ data (* j 4))))
              (mem-set (+ data (* j 4)) (mem-get (+ data (* (+ j 1) 4))))
              (mem-set (+ data (* (+ j 1) 4)) t)))
        (setq j (+ j 1))))
    (setq i (+ i 1))))

(setq i 0)
(while (< i n)
  (do
    (print-int (mem-get (+ data (* i 4))))
    (if (< i (- n 1)) (print-char 32))
    (setq i (+ i 1))))
(print-char 10)
