import React, { useState, useEffect } from 'react';
import { X, RefreshCw } from 'lucide-react';
import './CallDetailsModal.css';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const CallDetailsModal = ({ call, onClose }) => {
  const [callDetails, setCallDetails] = useState(call || null);
  const [intelligence, setIntelligence] = useState(call?.intelligence || null);
  const [loading, setLoading] = useState(false);
  const [isReprocessing, setIsReprocessing] = useState(false);

  useEffect(() => {
    if (call) {
      setCallDetails(call);
      setIntelligence(call.intelligence || null);
      
      const fetchId = call.provider_call_id || call.frappe_call_log_id;
      if (fetchId) {
        setLoading(true);
        fetch(`${API_BASE_URL}/api/v1/dashboard/calls/${encodeURIComponent(fetchId)}/details`)
          .then(res => {
            if (!res.ok) {
              // Fallback to intelligence endpoint if details endpoint not present
              return fetch(`${API_BASE_URL}/api/v1/dashboard/calls/${encodeURIComponent(fetchId)}/intelligence`)
                .then(r => r.ok ? r.json() : null)
                .then(intel => intel ? { intelligence: intel } : null);
            }
            return res.json();
          })
          .then(data => {
            if (data) {
              if (data.intelligence) setIntelligence(data.intelligence);
              if (data.transcript) setCallDetails(prev => ({ ...prev, ...data }));
            }
          })
          .catch(err => {
            console.error("Failed to fetch detailed call data:", err);
          })
          .finally(() => {
            setLoading(false);
          });
      }
    }
  }, [call]);

  const handleReprocess = async () => {
    const fetchId = call?.provider_call_id || call?.frappe_call_log_id;
    if (!fetchId || isReprocessing) return;

    setIsReprocessing(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/dashboard/calls/${encodeURIComponent(fetchId)}/reprocess`, {
        method: 'POST'
      });
      if (res.ok) {
        const data = await res.json();
        if (data.intelligence) setIntelligence(data.intelligence);
        if (data.transcript) setCallDetails(prev => ({ ...prev, ...data }));
      }
    } catch (err) {
      console.error("Failed to reprocess call:", err);
    } finally {
      setIsReprocessing(false);
    }
  };

  if (!call) return null;

  return (
    <div className="modal active">
      <div className="modal-content">
        <div className="modal-header">
          <h2>Call Intelligence Details</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button 
              className="reprocess-btn" 
              onClick={handleReprocess}
              disabled={isReprocessing || loading}
              title="Re-run Speech-to-Text and AI Intelligence on audio recording"
            >
              <RefreshCw size={14} className={isReprocessing ? "spin" : ""} />
              {isReprocessing ? "Analyzing..." : "Re-analyze Audio"}
            </button>
            <button className="close-btn" onClick={onClose}>
              <X size={24} />
            </button>
          </div>
        </div>
        <div className="modal-body">
          {loading ? (
            <div style={{ textAlign: 'center', padding: '2rem' }}>
              <i className="spinner" style={{ borderTopColor: 'var(--accent-primary)' }}></i>
              <div style={{ marginTop: '1rem', color: 'var(--text-secondary)' }}>
                Fetching Call Intelligence...
              </div>
            </div>
          ) : intelligence ? (
            <>
              <div className="intel-grid">
                <div className="intel-item">
                  <div className="intel-label">Call Outcome</div>
                  <div className="intel-value">{intelligence.call_outcome || 'N/A'}</div>
                </div>
                <div className="intel-item">
                  <div className="intel-label">Lead Quality</div>
                  <div className="intel-value">{intelligence.lead_quality || 'N/A'}</div>
                </div>
                <div className="intel-item">
                  <div className="intel-label">Follow-up At</div>
                  <div className="intel-value">
                    {intelligence.follow_up_at ? new Date(intelligence.follow_up_at).toLocaleString() : 'Not Scheduled'}
                  </div>
                </div>
                <div className="intel-item">
                  <div className="intel-label">Review Flag</div>
                  <div className="intel-value" style={{ color: intelligence.review_flag ? 'var(--danger)' : 'var(--success)' }}>
                    {intelligence.review_flag ? 'Needs Review' : 'OK'}
                  </div>
                </div>
              </div>

              <div style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem', color: '#fff' }}>Summary</h3>
                <p style={{ color: 'var(--text-primary)', lineHeight: '1.5' }}>
                  {intelligence.call_summary || 'No summary available.'}
                </p>
              </div>

              <div style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem', color: '#fff' }}>Customer Intent</h3>
                <p style={{ color: 'var(--text-primary)', lineHeight: '1.5' }}>
                  {intelligence.customer_intent || 'N/A'}
                </p>
              </div>

              {intelligence.key_points && intelligence.key_points.length > 0 && (
                <div style={{ marginBottom: '1.5rem' }}>
                  <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem', color: '#fff' }}>Key Points</h3>
                  <ul style={{ color: 'var(--text-primary)', lineHeight: '1.8', paddingLeft: '1.2rem' }}>
                    {intelligence.key_points.map((pt, i) => <li key={i}>{pt}</li>)}
                  </ul>
                </div>
              )}

              <div style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem', color: '#fff' }}>Next Action</h3>
                <p style={{ color: 'var(--text-primary)', lineHeight: '1.5' }}>
                  {intelligence.next_action || 'N/A'}
                </p>
              </div>

              <div style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem', color: '#fff' }}>Audio Recording</h3>
                <audio 
                  controls 
                  src={`${API_BASE_URL}/api/v1/dashboard/calls/${encodeURIComponent(call.provider_call_id || call.frappe_call_log_id)}/recording`}
                  style={{ width: '100%', height: '40px', outline: 'none' }}
                >
                  Your browser does not support the audio element.
                </audio>
              </div>

              <div>
                <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem', color: '#fff' }}>Audio Transcript</h3>
                <div className="transcript-box">
                  {callDetails?.transcript || call?.transcript || 'No transcript available.'}
                </div>
              </div>
            </>
          ) : (
            <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)' }}>
              No intelligence data found for this call.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default CallDetailsModal;
