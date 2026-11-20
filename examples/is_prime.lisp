(defun is-prime (n)
  (if (< n 2)
      0
      (do
        (setq i 2)
        (setq result 1)
        (while (and (<= (* i i) n) (= result 1))
          (do
            (if (= (% n i) 0)
                (setq result 0))
            (setq i (+ i 1))))
        result)))

(print-int (is-prime (read-int-line)))
(print-char 10)
