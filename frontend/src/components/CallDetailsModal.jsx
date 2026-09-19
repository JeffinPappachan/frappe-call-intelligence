import React, { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import './CallDetailsModal.css';

const CallDetailsModal = ({ call, onClose }) => {
  const [intelligence, setIntelligence] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (call) {
      // Use existing intelligence if available initially
      setIntelligence(call.intelligence || null);
      
      const fetchId = call.provider_call_id || call.frappe_call_log_id;
      if (fetchId) {
        setLoading(true);
        const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
        fetch(`${API_BASE_URL}/api/v1/dashboard/calls/${encodeURIComponent(fetchId)}/intelligence`)
          .then(res => res.json())
          .then(data => {
            setIntelligence(data);
          })
          .catch(err => {
            console.error("Failed to fetch detailed intelligence:", err);
          })
          .finally(() => {
            setLoading(false);
          });
      }
    }
  }, [call]);

  if (!call) return null;

  return (
    <div className="modal active">
      <div className="modal-content">
        <div className="modal-header">
          <h2>Call Intelligence Details</h2>
          <button className="close-btn" onClick={onClose}>
            <X size={24} />
          </button>
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
                  {intelligence.summary || 'No summary available.'}
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
                  {intelligence.audio_transcript || 'No transcript available.'}
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
