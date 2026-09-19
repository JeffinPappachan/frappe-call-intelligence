-- Create calls table
CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_call_id VARCHAR UNIQUE NOT NULL,
    direction VARCHAR,
    duration_seconds INT,
    agent_id VARCHAR,
    matched_lead_id VARCHAR,
    frappe_call_log_id VARCHAR,
    frappe_task_id VARCHAR,
    recording_storage_path VARCHAR,
    transcript TEXT,
    event_timestamp TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Create call_intelligence table
CREATE TABLE call_intelligence (
    call_id UUID PRIMARY KEY REFERENCES calls(id) ON DELETE CASCADE,
    call_summary TEXT,
    call_outcome VARCHAR,
    lead_quality VARCHAR,
    customer_intent VARCHAR,
    primary_objection VARCHAR,
    objections JSONB,
    key_points JSONB,
    next_action VARCHAR,
    recommended_action VARCHAR,
    follow_up_required BOOLEAN,
    follow_up_date TIMESTAMPTZ,
    agent_quality_notes TEXT,
    review_flag BOOLEAN
);

-- Enable RLS (Optional but recommended)
ALTER TABLE calls ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_intelligence ENABLE ROW LEVEL SECURITY;

-- Create policy to allow full access for service_role key
-- If using anonymous client, change policy appropriately
CREATE POLICY "Allow all operations" ON calls FOR ALL USING (true);
CREATE POLICY "Allow all operations" ON call_intelligence FOR ALL USING (true);

-- Insert into storage buckets if it doesn't exist
INSERT INTO storage.buckets (id, name, public) 
VALUES ('recordings', 'recordings', false)
ON CONFLICT (id) DO NOTHING;
