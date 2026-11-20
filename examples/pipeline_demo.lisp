(defun sq (x)
  (* x x))

(setq i 1)
(setq s 0)
(while (<= i 100)
  (do
    (setq s (+ s (sq i)))
    (setq i (+ i 1))))

(print-int s)
(print-char 10)
