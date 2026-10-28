-- P08 readiness: CS window grain per (line, station): active loads, conflicting rows and offset signs (read-only).
-- Two active LOAD_IDs or disagreeing duplicate rows block that (line, station) (no MAX(load)); signed offsets
-- and reference positions are reported raw so the date-conversion policy (OD-05) can be confirmed.
SELECT w.LINE_NUMBER, w.CONTROL_STATION,
       COUNT(DISTINCT w.LOAD_ID) AS ACTIVE_LOADS,
       COUNT_BIG(*) AS ACTIVE_ROWS,
       COUNT(DISTINCT CONCAT(CONVERT(varchar(10), w.CS_OP_LOAD, 23), '|', CONVERT(varchar(10), w.CS_OP_UNLOAD, 23), '|',
                             w.LOAD_OFFSET, '|', w.UNLOAD_OFFSET, '|', w.LOAD_REF_POS, '|', w.UNLOAD_REF_POS, '|', w.MBU)) AS DISTINCT_SIGNATURES,
       MIN(w.LOAD_OFFSET) AS MIN_LOAD_OFFSET, MAX(w.LOAD_OFFSET) AS MAX_LOAD_OFFSET,
       MIN(w.UNLOAD_OFFSET) AS MIN_UNLOAD_OFFSET, MAX(w.UNLOAD_OFFSET) AS MAX_UNLOAD_OFFSET,
       SUM(CASE WHEN w.CS_OP_LOAD IS NULL THEN 1 ELSE 0 END) AS NULL_LOAD_DATES,
       SUM(CASE WHEN w.CS_OP_UNLOAD IS NULL THEN 1 ELSE 0 END) AS NULL_UNLOAD_DATES,
       MAX(w.LOAD_TS) AS LATEST_LOAD_TS
FROM WEB_APP.FOCUS_CS_WINDOWS w
WHERE w.IS_ACTIVE = 1
GROUP BY w.LINE_NUMBER, w.CONTROL_STATION
ORDER BY w.LINE_NUMBER, w.CONTROL_STATION;
