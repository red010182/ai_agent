---
scenario: productivity_lost
case_id: case_1
title: Scanner Lost
keywords:
  - scanner lost
  - foup exchange
  - productivity
  - scanner 異常
jumps_to:
  - case_2
  - case_3
---

## case 1

### symptom
Scanner lost，產能下降或停線。

### question
foup exchange 是否有派滿？

### action
1. 查看 A 系統 GUI，確認 scanner 狀態欄位是否顯示 offline
2. 檢查最近 1 小時的 scanner log：
```sql
SELECT event_time, equipment_id, event_code, event_desc
FROM equipment_event_log
WHERE equipment_id = '{equipment_id}'
  AND event_time > NOW() - INTERVAL '1 hour'
ORDER BY event_time DESC
LIMIT 50
```
3. 確認是否有 foup 卡在 port：
```sql
SELECT port_id, foup_id, status, last_updated
FROM foup_status
WHERE equipment_id = '{equipment_id}'
  AND status != 'normal'
```
4. 若以上查詢皆無異常，重新確認 scanner 電源狀態

### note
如果步驟 2 查詢沒有結果，可以考慮 case 2（確認網路連線問題）。
如果有 foup 卡住，可以考慮 case 3（foup 異常排查）。

---

## case 2

### symptom
Scanner log 查無資料，疑似網路或通訊異常。

### question
設備最後一次正常通訊是什麼時候？

### action
1. 查詢設備通訊歷史：
```sql
SELECT equipment_id, last_heartbeat, connection_status, host_ip
FROM equipment_connection
WHERE equipment_id = '{equipment_id}'
```
2. 確認 host 端的網路設定是否有異動：
```sql
SELECT changed_at, changed_by, old_value, new_value, field_name
FROM system_config_log
WHERE equipment_id = '{equipment_id}'
  AND changed_at > '{start_time}'
ORDER BY changed_at DESC
```
3. 通知 IT 檢查網路設備

### note
若通訊記錄正常但 scanner 仍 lost，回到 case 1 重新確認。

---

## case 3

### symptom
Foup 卡在 port 或狀態異常，導致 scanner 無法正常運作。

### question
哪個 port 的 foup 出現異常？port 編號為何？

### action
1. 查詢該 port 的詳細狀態：
```sql
SELECT port_id, foup_id, lot_id, status, error_code, last_updated
FROM foup_status
WHERE equipment_id = '{equipment_id}'
  AND port_id = '{port_id}'
```
2. 查詢該 foup 的移動歷史：
```sql
SELECT action_time, action_type, from_location, to_location, operator_id
FROM foup_movement_log
WHERE foup_id = '{foup_id}'
  AND action_time > NOW() - INTERVAL '2 hour'
ORDER BY action_time DESC
```
3. 依 error_code 查詢對應處理方式：
```sql
SELECT error_code, error_desc, recommended_action
FROM error_code_reference
WHERE error_code = '{error_code}'
```
4. 依建議處理方式執行，若無法自行處理則通知設備工程師

### note
若 error_code 查無對應說明，需人工處理，通知設備工程師現場確認。
