-- Add missing fields
ALTER TABLE call_intelligence ADD COLUMN follow_up_notes TEXT;
ALTER TABLE call_intelligence ADD COLUMN follow_up_at TIMESTAMPTZ;
ALTER TABLE calls ADD COLUMN call_type VARCHAR;

-- Remove permissive RLS policies
DROP POLICY "Allow all operations" ON calls;
DROP POLICY "Allow all operations" ON call_intelligence;
