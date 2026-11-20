(setq name (buf 64))

(print-str "What is your name? ")
(setq len (read-line-limited name 64))
(if (= len -1)
    (print-str "-1")
    (do
      (print-str "Hello, ")
      (print-str name)
      (print-str "!")))
(print-char 10)
