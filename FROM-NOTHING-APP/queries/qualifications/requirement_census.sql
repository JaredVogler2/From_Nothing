-- P06 readiness: requirement census per in-scope job (read-only, explicit projection, no SELECT *).
-- A job with positive NEO rows is REQUIRED; a job without rows is UNKNOWN here. Only the separate
-- affirmative census feed (supplemental requirement_census) can make it AFFIRMATIVELY_NONE; missing
-- rows never mean cert-free, and NC/PU/CI rework never clears requirements.
-- Scans every non-COMPLETE job; the caller intersects the result with the admitted scope receipt.
WITH Jobs AS (
    SELECT DISTINCT m.LINE_NUMBER, m.SHOP_ORDER_IDENTIFIER AS SOI_ID, m.IS_REWORK
    FROM WEB_APP.FOCUS_MASTER_TASK_LIST m
    WHERE m.SHOP_ORDER_STATUS <> 'COMPLETE'
), Positive AS (
    SELECT r.LINE_NUMBER, r.SOI_ID, COUNT_BIG(*) AS RowCount, COUNT(DISTINCT r.CERT_CODE) AS DistinctCerts,
           MAX(r.SNAPSHOT_TS) AS LatestSnapshot
    FROM WEB_APP.NEO_SOI_CERT_REQUIREMENTS r
    WHERE r.REQ_CERT = 'Y' OR r.REQ_CERT IS NULL
    GROUP BY r.LINE_NUMBER, r.SOI_ID
)
SELECT j.LINE_NUMBER, j.SOI_ID, j.IS_REWORK,
       COALESCE(p.RowCount, 0) AS POSITIVE_ROWS, COALESCE(p.DistinctCerts, 0) AS DISTINCT_CERTS, p.LatestSnapshot AS LATEST_SNAPSHOT_TS,
       CASE WHEN p.RowCount > 0 THEN 'REQUIRED' ELSE 'UNKNOWN' END AS CENSUS_STATUS
FROM Jobs j
LEFT JOIN Positive p ON p.LINE_NUMBER = j.LINE_NUMBER AND p.SOI_ID = j.SOI_ID
ORDER BY j.LINE_NUMBER, j.SOI_ID;
