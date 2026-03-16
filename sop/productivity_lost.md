---
scenario: productivity_lost
cases:
  - case_id: case_1
    title: Tool Scanner Lost
    keywords:
      - scanner lost
      - tool offline
      - productivity lost
      - xxx issue
    jumps_to: [case_2, case_3]

  - case_id: case_2
    title: Scanner Lost + 網路通訊異常
    keywords:
      - scanner lost
      - 通訊異常
      - log 查無資料
      - heartbeat lost
    jumps_to: [case_1]

  - case_id: case_3
    title: Foup 卡在 Port 或狀態異常
    keywords:
      - foup stuck
      - foup error
      - port abnormal
      - foup 異常
    jumps_to: []
---

## case 1

### symptom
xxx issue，產能下降或停線。

### problem_to_verify
foup exchange 是否有派滿？

### how_to_verify
1. 查看 A 系統 GUI，確認 xxx 狀態欄位是否顯示 offline
2. 檢查最近 1 小時的 xxx log：
```sql
SELECT event_time, equipment_id, event_code, event_desc
FROM equipment_event_log
WHERE equipment_id = &equipment_id
  AND event_time > NOW() - INTERVAL '1 hour'
ORDER BY event_time DESC
LIMIT 50
```
- 若查詢無結果 → 走 case 2（疑似網路或通訊異常）
- 若查詢有結果，繼續步驟 3

3. 確認是否有 foup 卡在 port：
```sql
SELECT port_id, foup_id, status, last_updated
FROM foup_status
WHERE equipment_id = &equipment_id
  AND status != 'normal'
```
- 若有異常 foup → 走 case 3
- 若無異常 → 重新確認 xxx 電源狀態，流程結束

### note
步驟 2 與步驟 3 需依序執行，先排除通訊問題再確認 foup 狀態。

---

## case 2

### symptom
xxx log 查無資料，疑似網路或通訊異常。

### problem_to_verify
設備最後一次正常通訊是什麼時候？

### how_to_verify
1. 查詢設備通訊歷史：
```sql
SELECT equipment_id, last_heartbeat, connection_status, host_ip
FROM equipment_connection
WHERE equipment_id = &equipment_id
```
2. 確認 host 端的網路設定是否有異動：
```sql
SELECT changed_at, changed_by, old_value, new_value, field_name
FROM system_config_log
WHERE equipment_id = &equipment_id
  AND changed_at > &start_time
ORDER BY changed_at DESC
```
- 若有網路設定異動 → 通知 IT 檢查，流程結束
- 若無異動但通訊仍異常 → 走 case 1 重新確認

3. 通知 IT 檢查網路設備

### note
需同時提供 equipment_id 與 start_time（建議取事件發生前 24 小時）。

---

## case 3

### symptom
Foup 卡在 port 或狀態異常，導致 xxx 無法正常運作。

### problem_to_verify
哪個 port 的 foup 出現異常？port 編號為何？

### how_to_verify
1. 查詢該 port 的詳細狀態：
```sql
SELECT port_id, foup_id, lot_id, status, error_code, last_updated
FROM foup_status
WHERE equipment_id = &equipment_id
  AND port_id = &port_id
```
2. 查詢該 foup 的移動歷史：
```sql
SELECT action_time, action_type, from_location, to_location, operator_id
FROM foup_movement_log
WHERE foup_id = &foup_id
  AND action_time > NOW() - INTERVAL '2 hour'
ORDER BY action_time DESC
```
3. 依 error_code 查詢對應處理方式：
```sql
SELECT error_code, error_desc, recommended_action
FROM error_code_reference
WHERE error_code = &error_code
```
- 有對應 recommended_action → 依建議處理，流程結束
- 無對應說明 → human_handoff，通知設備工程師現場確認

### note
若 error_code 查無對應說明，需人工處理，通知設備工程師現場確認。
