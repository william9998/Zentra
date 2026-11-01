-- ZEN-000：runs 補 result_received_at
--
-- save_result 用這個欄位做結果冪等（沙盒重送同一份結果要接受，送不一樣的要擋）。
-- 模型有、0001 沒建 —— 這是 0001 之後才加的欄位，所以補一個 0002，
-- 不改已套用的 0001。

ALTER TABLE runs ADD COLUMN result_received_at TIMESTAMP WITH TIME ZONE;
