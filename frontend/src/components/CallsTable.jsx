import React, { useState } from 'react';
import { AlertCircle } from 'lucide-react';
import './CallsTable.css';

const CallsTable = ({ calls, onRowClick }) => {
  const [currentPage, setCurrentPage] = useState(1);
  const rowsPerPage = 10;

  if (!calls || calls.length === 0) {
    return (
      <div className="glass-panel table-container">
        <h3>Recent Calls</h3>
        <table>
          <thead>
            <tr>
              <th>Sl. No</th>
              <th>Date & Time</th>
              <th>Lead</th>
              <th>Agent</th>
              <th>Duration</th>
              <th>Outcome</th>
              <th>Quality</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td colSpan="8" style={{ textAlign: 'center' }}>No calls processed yet.</td>
            </tr>
          </tbody>
        </table>
      </div>
    );
  }

  const totalPages = Math.ceil(calls.length / rowsPerPage);
  const start = (currentPage - 1) * rowsPerPage;
  const end = Math.min(start + rowsPerPage, calls.length);
  const pageData = calls.slice(start, end);

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    const d = new Date(dateString);
    return d.toLocaleString();
  };

  const getQualityBadge = (quality) => {
    if (!quality) return null;
    const map = {
      'Hot': 'badge-hot',
      'Warm': 'badge-warm',
      'Cold': 'badge-cold'
    };
    const cls = map[quality] || '';
    return <span className={`badge ${cls}`}>{quality}</span>;
  };

  return (
    <div className="glass-panel table-container">
      <h3>Recent Calls</h3>
      <table>
        <thead>
          <tr>
            <th>Sl. No</th>
            <th>Date & Time</th>
            <th>Lead</th>
            <th>Agent</th>
            <th>Duration</th>
            <th>Outcome</th>
            <th>Quality</th>
            <th>Review</th>
          </tr>
        </thead>
        <tbody>
          {pageData.map((call, index) => {
            const globalIndex = start + index;
            
            let outcome = <span style={{ color: 'var(--text-secondary)' }}>Not analyzed</span>;
            let quality = <span style={{ color: 'var(--text-secondary)' }}>Not analyzed</span>;
            let review = <span style={{ color: 'var(--text-secondary)' }}>Not analyzed</span>;

            if (call.intelligence) {
              outcome = call.intelligence.call_outcome || '-';
              quality = getQualityBadge(call.intelligence.lead_quality) || '-';
              review = call.intelligence.review_flag ? (
                <span className="badge badge-review">
                  <AlertCircle size={12} /> Review
                </span>
              ) : 'OK';
            }

            return (
              <tr key={call.provider_call_id || globalIndex} onClick={() => onRowClick(call)}>
                <td>{globalIndex + 1}</td>
                <td>{formatDate(call.event_timestamp)}</td>
                <td>{call.matched_lead || 'Unknown'}</td>
                <td>{call.agent_id || 'Unknown'}</td>
                <td>{call.duration_seconds || 0}s</td>
                <td>{outcome}</td>
                <td>{quality}</td>
                <td>{review}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {totalPages > 1 && (
        <div className="pagination">
          <button 
            disabled={currentPage === 1} 
            onClick={() => setCurrentPage(prev => Math.max(prev - 1, 1))}
          >
            &laquo; Prev
          </button>
          
          {Array.from({ length: totalPages }, (_, i) => i + 1).map(p => (
            <button 
              key={p} 
              className={p === currentPage ? 'active' : ''} 
              onClick={() => setCurrentPage(p)}
            >
              {p}
            </button>
          ))}
          
          <button 
            disabled={currentPage === totalPages} 
            onClick={() => setCurrentPage(prev => Math.min(prev + 1, totalPages))}
          >
            Next &raquo;
          </button>
        </div>
      )}
    </div>
  );
};

export default CallsTable;
