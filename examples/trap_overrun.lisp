(setq eof 0)

(defun on-input ()
  (do
    (setq c (in 0))
    (if (= c -1)
        (setq eof 1)
        (out 1 c))))

(interrupt-handler on-input 0)

(while (= eof 0) (do))
(print-char 10)
