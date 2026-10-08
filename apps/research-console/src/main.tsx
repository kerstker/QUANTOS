/// <reference types="vite/client" />
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from '@/lib/router';
import App from './App';
import './studio/studio.css';
import { getConsoleRuntimeMode } from '@/lib/api/research-console-api';

console.log(`[QUANTOS Research Console] Mode: ${getConsoleRuntimeMode()}`);

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
