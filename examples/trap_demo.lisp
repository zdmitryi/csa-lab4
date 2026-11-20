(setq eof 0)
(setq nested 0)

(defun on-input ()
  (do
    (setq c (in 0))
    (if (= c -1)
        (setq eof 1)
        (do
          (out 1 c)
          (if (= c 65)
              (do
                (ei)
                (while (= nested 0) (do))
                (out 1 62))
              (setq nested 1))))))

(interrupt-handler on-input 0)

(while (= eof 0) (do))
(print-char 10)
