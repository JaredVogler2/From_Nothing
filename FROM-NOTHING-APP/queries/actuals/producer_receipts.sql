-- Draft reader (P17): per-feed producer receipts. An empty feed is covered only when its receipt is complete and either
-- certified empty or carries a covered-through watermark; anything else stays EMPTY_UNCERTIFIED and withholds official results.
SELECT r.Feed AS FEED, r.Complete AS COMPLETE, r.CertifiedEmpty AS CERTIFIED_EMPTY, r.CoveredThroughUtc AS COVERED_THROUGH_UTC,
 r.Producer AS PRODUCER, r.UpstreamRevision AS UPSTREAM_REVISION, r.UpdatedAtUtc AS UPDATED_AT_UTC
FROM WEB_APP.FOCUS_FN_ObservationProducerReceipt r
ORDER BY r.Feed;
