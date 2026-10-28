-- P06 readiness: holding status and date-encoding profile (read-only; no names or person keys).
-- Calibrates qualifications.accepted_date_forms / perpetual_sentinel / status vocabularies before
-- live admission. Encodings: slash YYYY/MM/DD, ISO YYYY-MM-DD, compact YYYYMMDD; anything else is
-- MALFORMED and fails eligibility (never unlimited authorization).
SELECT c.STATUS,
       CASE
           WHEN c.CERT_EXP_DATE IS NULL OR LTRIM(RTRIM(c.CERT_EXP_DATE)) = '' THEN 'BLANK'
           WHEN c.CERT_EXP_DATE IN ('9999/12/31', '9999-12-31', '99991231') THEN 'SENTINEL_CANDIDATE'
           WHEN c.CERT_EXP_DATE LIKE '[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]' THEN 'YYYY/MM/DD'
           WHEN c.CERT_EXP_DATE LIKE '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN 'YYYY-MM-DD'
           WHEN c.CERT_EXP_DATE LIKE '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' THEN 'YYYYMMDD'
           ELSE 'MALFORMED'
       END AS EXPIRY_FORM,
       CASE WHEN TRY_CONVERT(date, REPLACE(c.CERT_EXP_DATE, '/', '-'), 23) IS NULL
                 AND TRY_CONVERT(date, c.CERT_EXP_DATE, 112) IS NULL THEN 0 ELSE 1 END AS EXPIRY_PARSES,
       COUNT_BIG(*) AS HOLDINGS,
       COUNT(DISTINCT c.CERT_CODE) AS DISTINCT_CERTS,
       MAX(c.SNAPSHOT_TS) AS LATEST_SNAPSHOT_TS
FROM WEB_APP.FOCUS_MECHANIC_CERTS c
GROUP BY c.STATUS,
         CASE
             WHEN c.CERT_EXP_DATE IS NULL OR LTRIM(RTRIM(c.CERT_EXP_DATE)) = '' THEN 'BLANK'
             WHEN c.CERT_EXP_DATE IN ('9999/12/31', '9999-12-31', '99991231') THEN 'SENTINEL_CANDIDATE'
             WHEN c.CERT_EXP_DATE LIKE '[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]' THEN 'YYYY/MM/DD'
             WHEN c.CERT_EXP_DATE LIKE '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN 'YYYY-MM-DD'
             WHEN c.CERT_EXP_DATE LIKE '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' THEN 'YYYYMMDD'
             ELSE 'MALFORMED'
         END,
         CASE WHEN TRY_CONVERT(date, REPLACE(c.CERT_EXP_DATE, '/', '-'), 23) IS NULL
                   AND TRY_CONVERT(date, c.CERT_EXP_DATE, 112) IS NULL THEN 0 ELSE 1 END
ORDER BY c.STATUS, EXPIRY_FORM;
