import React from 'react';
import './KPICards.css';

const KPICards = ({ metrics }) => {
  return (
    <div className="kpi-grid">
      <div className="glass-panel kpi-card">
        <div className="kpi-title">Total Calls</div>
        <div className="kpi-value">{metrics.total_calls ?? '-'}</div>
      </div>
      <div className="glass-panel kpi-card">
        <div className="kpi-title">Completed</div>
        <div className="kpi-value" style={{ color: 'var(--success)' }}>
          {metrics.completed_calls ?? '-'}
        </div>
      </div>
      <div className="glass-panel kpi-card">
        <div className="kpi-title">Missed/Failed</div>
        <div className="kpi-value" style={{ color: 'var(--danger)' }}>
          {metrics.missed_calls ?? '-'}
        </div>
      </div>
      <div className="glass-panel kpi-card">
        <div className="kpi-title">Avg Duration (sec)</div>
        <div className="kpi-value">{metrics.average_call_duration_seconds ?? '-'}</div>
      </div>
      <div className="glass-panel kpi-card">
        <div className="kpi-title">Follow-ups Due</div>
        <div className="kpi-value" style={{ color: 'var(--warning)' }}>
          {metrics.follow_ups_due !== undefined 
            ? `${metrics.follow_ups_due} (+${metrics.follow_ups_overdue} overdue)` 
            : '-'}
        </div>
      </div>
    </div>
  );
};

export default KPICards;
