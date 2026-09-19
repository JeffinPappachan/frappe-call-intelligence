import React, { useState, useEffect } from 'react';
import { X, Upload, FileAudio } from 'lucide-react';
import './UploadModal.css';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const UploadModal = ({ isOpen, onClose, onUpload }) => {
  const [file, setFile] = useState(null);
  const [contacts, setContacts] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loadingOptions, setLoadingOptions] = useState(true);
  
  // Form State
  const [selectedContact, setSelectedContact] = useState('');
  const [customContact, setCustomContact] = useState('');
  const [leadPhone, setLeadPhone] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('');
  const [customAgent, setCustomAgent] = useState('');
  const [agentPhone, setAgentPhone] = useState('');
  const [callType, setCallType] = useState('outbound');
  const [callDate, setCallDate] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  
  useEffect(() => {
    if (isOpen) {
      fetchOptions();
      
      // Reset form
      setFile(null);
      setSelectedContact('');
      setCustomContact('');
      setLeadPhone('');
      setSelectedAgent('');
      setCustomAgent('');
      setCallType('outbound');
      setCallDate(new Date().toISOString().slice(0, 16));
    }
  }, [isOpen]);

  const fetchOptions = async () => {
    setLoadingOptions(true);
    try {
      const [contactsRes, agentsRes] = await Promise.all([
        fetch(`${API_BASE_URL}/api/v1/crm/contacts`, { cache: 'no-store' }),
        fetch(`${API_BASE_URL}/api/v1/crm/agents`, { cache: 'no-store' })
      ]);
      const contactsData = await contactsRes.json();
      const agentsData = await agentsRes.json();
      
      setContacts(contactsData.data || []);
      setAgents(agentsData.data || []);
      
      if (agentsData.data && agentsData.data.length > 0) {
        setSelectedAgent(agentsData.data[0].name);
      }
    } catch (err) {
      console.error("Failed to fetch CRM options:", err);
    } finally {
      setLoadingOptions(false);
    }
  };

  const handleContactChange = (e) => {
    const contactId = e.target.value;
    setSelectedContact(contactId);
    
    if (contactId && contactId !== 'custom') {
      setCustomContact('');
      if (contactId.startsWith('saved-')) {
        setLeadPhone(contactId.replace('saved-', ''));
      } else {
        const contact = contacts.find(c => c.name === contactId);
        if (contact && contact.mobile_no) {
          setLeadPhone(contact.mobile_no);
        }
      }
    } else if (contactId === 'custom') {
      setCustomContact('');
    } else {
      setLeadPhone('');
      setCustomContact('');
    }
  };

  const handleAgentChange = (e) => {
    const agId = e.target.value;
    setSelectedAgent(agId);
    
    if (agId && agId !== 'custom') {
      const agent = agents.find(a => a.name === agId);
      if (agent && agent.mobile_no) {
        setAgentPhone(agent.mobile_no);
      } else {
        setAgentPhone('');
      }
    } else {
      setAgentPhone('');
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file || !leadPhone) return;

    setIsUploading(true);
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('lead_phone', leadPhone);
    if (selectedContact === 'custom' && customContact) {
      formData.append('lead_name', customContact);
    } else if (selectedContact && selectedContact !== 'custom' && !selectedContact.startsWith('saved-')) {
      formData.append('lead_id', selectedContact);
      const contact = contacts.find(c => c.name === selectedContact);
      if (contact && contact.lead_name) {
        formData.append('lead_name', contact.lead_name);
      }
    } else if (selectedContact && selectedContact.startsWith('saved-')) {
      const contact = contacts.find(c => c.name === selectedContact);
      if (contact && contact.lead_name && contact.lead_name !== 'Custom') {
        formData.append('lead_name', contact.lead_name);
      }
    }
    
    if (selectedAgent === 'custom' && customAgent) {
      formData.append('agent_id', customAgent);
    } else if (selectedAgent && selectedAgent.startsWith('saved-')) {
      formData.append('agent_id', selectedAgent.replace('saved-', ''));
    } else if (selectedAgent && selectedAgent !== 'custom') {
      formData.append('agent_id', selectedAgent);
    }
    if (agentPhone) {
      formData.append('agent_phone', agentPhone);
    }
    formData.append('direction', callType);
    if (callDate) {
      formData.append('event_timestamp', new Date(callDate).toISOString());
    }

    try {
      await onUpload(formData);
      onClose();
    } catch (err) {
      console.error(err);
    } finally {
      setIsUploading(false);
    }
  };

  if (!isOpen) return null;

  const isFormValid = Boolean(
    file &&
    leadPhone.trim() &&
    (selectedContact !== 'custom' || customContact.trim()) &&
    (selectedAgent !== 'custom' || customAgent.trim()) &&
    !isUploading
  );

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-header">
          <h2>Upload Manual Call Recording</h2>
          <button className="close-btn" onClick={onClose}><X size={20} /></button>
        </div>
        
        <form onSubmit={handleSubmit} className="upload-form">
          <div className="upload-form-body">
            <div className="form-group file-upload-area">
              <label htmlFor="audio-file" className="file-label">
                <FileAudio size={48} className="text-gray-400" />
                <div className="mt-2">
                  <span className="file-name-btn">{file ? file.name : "Select Audio File"}</span>
                </div>
                <input 
                  id="audio-file"
                  type="file" 
                  accept="audio/*" 
                  onChange={(e) => setFile(e.target.files[0])}
                  required
                  className="hidden"
                />
              </label>
            </div>

            <div className="form-group">
              <label>
                {callType === 'inbound' ? 'Caller Name (CRM Contact)' : 'Receiver Name (CRM Contact)'}
              </label>
              <select 
                value={selectedContact} 
                onChange={handleContactChange}
                disabled={loadingOptions}
              >
                <option value="">-- Select CRM Contact --</option>
                {contacts.map(c => (
                  <option key={c.name} value={c.name}>
                    {c.lead_name} {c.organization ? `(${c.organization})` : ''} - {c.mobile_no}
                  </option>
                ))}
                <option value="custom">-- Add Custom Name --</option>
              </select>
            </div>

            {selectedContact === 'custom' && (
              <div className="form-group">
                <label>
                  {callType === 'inbound' ? 'Custom Caller Name' : 'Custom Receiver Name'} <span className="required">*</span>
                </label>
                <input 
                  type="text" 
                  value={customContact} 
                  onChange={(e) => setCustomContact(e.target.value)} 
                  placeholder="E.g. John Doe"
                  required
                />
              </div>
            )}

            <div className="form-row">
              <div className="form-group">
                <label>
                  {callType === 'inbound' ? 'Receiver Name (Agent)' : 'Caller Name (Agent)'}
                </label>
                <select 
                  value={selectedAgent} 
                  onChange={handleAgentChange}
                  disabled={loadingOptions}
                >
                  <option value="">-- Select Agent --</option>
                  {agents.map(a => (
                    <option key={a.name} value={a.name}>
                      {a.full_name || a.name}
                    </option>
                  ))}
                  <option value="custom">-- Add Custom Agent --</option>
                </select>
              </div>

              {selectedAgent === 'custom' && (
                <div className="form-group">
                  <label>Custom Agent Name <span className="required">*</span></label>
                  <input 
                    type="text" 
                    value={customAgent} 
                    onChange={(e) => setCustomAgent(e.target.value)} 
                    placeholder="E.g. Sarah Jenkins"
                    required
                  />
                </div>
              )}

              <div className="form-group">
                <label>Call Type</label>
                <select value={callType} onChange={(e) => setCallType(e.target.value)}>
                  <option value="outbound">Outbound</option>
                  <option value="inbound">Inbound</option>
                </select>
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>
                  From Number {callType === 'inbound' && <span className="required">*</span>}
                </label>
                <input 
                  type="text" 
                  value={callType === 'inbound' ? leadPhone : agentPhone} 
                  onChange={(e) => callType === 'inbound' ? setLeadPhone(e.target.value) : setAgentPhone(e.target.value)} 
                  placeholder="E.g. +15551234567"
                  required={callType === 'inbound'}
                />
              </div>
              <div className="form-group">
                <label>
                  To Number {callType === 'outbound' && <span className="required">*</span>}
                </label>
                <input 
                  type="text" 
                  value={callType === 'outbound' ? leadPhone : agentPhone} 
                  onChange={(e) => callType === 'outbound' ? setLeadPhone(e.target.value) : setAgentPhone(e.target.value)} 
                  placeholder="E.g. +15551234567"
                  required={callType === 'outbound'}
                />
              </div>
            </div>
            
            <div className="form-group">
              <label>Call Date / Time</label>
              <input 
                type="datetime-local" 
                value={callDate}
                onChange={(e) => setCallDate(e.target.value)}
              />
            </div>
          </div>

          <div className="modal-actions">
            <button type="button" onClick={onClose} disabled={isUploading}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={!isFormValid}>
              {isUploading ? 'Uploading...' : <><Upload size={16} /> Process Audio</>}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

export default UploadModal;
