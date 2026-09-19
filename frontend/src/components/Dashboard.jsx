import React, { useState, useEffect } from 'react';
import Header from './Header';
import KPICards from './KPICards';
import ChartsGrid from './ChartsGrid';
import CallsTable from './CallsTable';
import CallDetailsModal from './CallDetailsModal';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const Dashboard = () => {
  const [metrics, setMetrics] = useState({});
  const [calls, setCalls] = useState([]);
  const [selectedCall, setSelectedCall] = useState(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  const fetchMetrics = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/dashboard/metrics`);
      if (res.ok) {
        const data = await res.json();
        setMetrics(data);
      }
    } catch (err) {
      console.error("Failed to fetch metrics", err);
    }
  };

  const fetchCalls = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/dashboard/calls`);
      if (res.ok) {
        const data = await res.json();
        setCalls(data.calls || []);
      }
    } catch (err) {
      console.error("Failed to fetch calls", err);
    }
  };

  const refreshDashboard = async () => {
    setIsRefreshing(true);
    await Promise.all([
      fetchMetrics(), 
      fetchCalls(),
      new Promise(resolve => setTimeout(resolve, 1000)) // Min spinner delay
    ]);
    setIsRefreshing(false);
  };

  useEffect(() => {
    refreshDashboard();
    
    const intervalId = setInterval(() => {
      fetchMetrics();
      fetchCalls();
    }, 5000);

    return () => clearInterval(intervalId);
  }, []);

  const handleSimulateCall = async () => {
    try {
      await fetch(`${API_BASE_URL}/api/v1/telephony/webhook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_type: 'call.completed',
          call_id: `MOCK-${Math.random().toString(36).substring(7)}`,
          direction: 'inbound',
          from_number: '+919999999999',
          to_number: '+18001234567',
          duration_seconds: Math.floor(Math.random() * 120) + 30,
          recording_url: 'https://example.com/recording.mp3',
          timestamp: new Date().toISOString()
        })
      });
      refreshDashboard();
    } catch (err) {
      console.error("Simulation failed", err);
    }
  };

  const handleUploadAudio = async (formData) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/telephony/process-audio`, {
        method: 'POST',
        body: formData
      });
      if (!response.ok) {
        console.error("Upload returned non-OK status", await response.text());
      }
      // Brief delay to allow Supabase persistence to settle before refreshing
      await new Promise(resolve => setTimeout(resolve, 1500));
      await refreshDashboard();
    } catch (err) {
      console.error("Upload failed", err);
    }
  };

  return (
    <>
      <Header 
        onRefresh={refreshDashboard}
        onSimulate={handleSimulateCall}
        onUpload={handleUploadAudio}
        isRefreshing={isRefreshing}
      />
      
      <div className="container" style={{ maxWidth: '1400px', margin: '0 auto', padding: '2rem' }}>
        <KPICards metrics={metrics} />
        <ChartsGrid metrics={metrics} />
        <CallsTable calls={calls} onRowClick={setSelectedCall} />
      </div>

      {selectedCall && (
        <CallDetailsModal 
          call={selectedCall} 
          onClose={() => setSelectedCall(null)} 
        />
      )}
    </>
  );
};

export default Dashboard;
