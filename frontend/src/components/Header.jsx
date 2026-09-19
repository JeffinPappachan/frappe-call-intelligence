import React, { useState } from 'react';
import { PhoneCall, RefreshCw, Play, Upload } from 'lucide-react';
import UploadModal from './UploadModal';
import './Header.css';

const Header = ({ onRefresh, onSimulate, onUpload, isRefreshing }) => {
  const [isModalOpen, setIsModalOpen] = useState(false);

  const handleUploadClick = () => {
    setIsModalOpen(true);
  };

  return (
    <>
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
        <button onClick={onSimulate} className="btn-secondary" id="simulateBtn">
          <Play size={18} /> Simulate Call
        </button>
        <button onClick={handleUploadClick} className="btn-primary">
          <Upload size={18} /> Upload Audio
        </button>
      </div>
      </header>
      <UploadModal 
        isOpen={isModalOpen} 
        onClose={() => setIsModalOpen(false)} 
        onUpload={onUpload} 
      />
    </>
  );
};

export default Header;
