import React from 'react';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
  ArcElement
} from 'chart.js';
import { Bar, Doughnut } from 'react-chartjs-2';
import './ChartsGrid.css';

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
  ArcElement
);

ChartJS.defaults.color = '#7aa2f7';
ChartJS.defaults.font.family = 'Inter';

const ChartsGrid = ({ metrics }) => {
  if (!metrics || !metrics.calls_per_telecaller) return null;

  // Telecaller Bar Chart Data
  const agents = Object.keys(metrics.calls_per_telecaller);
  const callCounts = Object.values(metrics.calls_per_telecaller);
  
  const telecallerData = {
    labels: agents,
    datasets: [{
      label: 'Calls Processed',
      data: callCounts,
      backgroundColor: '#7aa2f7',
      borderRadius: 4
    }]
  };

  const barOptions = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      y: { beginAtZero: true, grid: { color: 'rgba(122,162,247,0.1)' } },
      x: { grid: { display: false } }
    },
    plugins: { legend: { display: false } }
  };

  // Quality Doughnut Chart Data
  const qualities = Object.keys(metrics.lead_quality_distribution || {});
  const qualCounts = Object.values(metrics.lead_quality_distribution || {});
  const qualityColors = {
    'Hot': '#f7768e',
    'Warm': '#e0af68',
    'Cold': '#9ece6a'
  };
  const qualBgColors = qualities.map(q => qualityColors[q] || '#bb9af7');

  const qualityData = {
    labels: qualities,
    datasets: [{
      data: qualCounts,
      backgroundColor: qualBgColors,
      borderWidth: 0
    }]
  };

  // Outcome Doughnut Chart Data
  const outcomes = Object.keys(metrics.call_outcome_distribution || {});
  const outCounts = Object.values(metrics.call_outcome_distribution || {});
  const outcomeColors = {
    'Interested': '#9ece6a',
    'Not Interested': '#f7768e',
    'Follow-up': '#e0af68',
    'Unknown': '#a9b1d6'
  };
  const outBgColors = outcomes.map(o => outcomeColors[o] || '#bb9af7');

  const outcomeData = {
    labels: outcomes,
    datasets: [{
      data: outCounts,
      backgroundColor: outBgColors,
      borderWidth: 0
    }]
  };

  const doughnutOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom' } },
    cutout: '70%'
  };

  return (
    <div className="dashboard-grid">
      <div className="glass-panel chart-container">
        <h3>Calls by Telecaller</h3>
        <div className="chart-wrapper">
          <Bar data={telecallerData} options={barOptions} />
        </div>
      </div>
      <div className="glass-panel chart-container">
        <h3>Lead Quality</h3>
        <div className="chart-wrapper">
          <Doughnut data={qualityData} options={doughnutOptions} />
        </div>
      </div>
      <div className="glass-panel chart-container">
        <h3>Call Outcomes</h3>
        <div className="chart-wrapper">
          <Doughnut data={outcomeData} options={doughnutOptions} />
        </div>
      </div>
    </div>
  );
};

export default ChartsGrid;
