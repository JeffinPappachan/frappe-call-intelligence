import React, { useRef } from 'react';
import { PhoneCall, RefreshCw, Play, Upload } from 'lucide-react';
import './Header.css';

const Header = ({ onRefresh, onSimulate, onUpload, isRefreshing }) => {
  const fileInputRef = useRef(null);

  const handleUploadClick = () => {
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    if (file && onUpload) {
      onUpload(file);
    }
    // Reset the input
    e.target.value = '';
  };

  return (
    <header className="app-header">
      <div className="logo">
        <PhoneCall size={24} />
        Manager Call Intelligence
      </div>
      <div className="controls">
        <button onClick={onRefresh} disabled={isRefreshing} id="refreshBtn">
          <RefreshCw size={18} className={isRefreshing ? 'spinner-icon' : ''} /> 
          Refresh
        </button>
        <button onClick={onSimulate} className="btn-primary" id="simulateBtn">
          <Play size={18} /> Simulate Call
        </button>
        <button onClick={handleUploadClick}>
          <Upload size={18} /> Upload Audio
        </button>
        <input 
          type="file" 
          ref={fileInputRef} 
          accept="audio/*" 
          className="hidden" 
          onChange={handleFileChange} 
        />
      </div>
    </header>
  );
};

export default Header;
