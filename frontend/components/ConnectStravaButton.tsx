'use client';
import { ExternalLink } from 'lucide-react';

export default function ConnectStravaButton() {
  const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';
  
  return (
    <a 
      href={`${API_BASE_URL}/api/auth/strava/login`}
      className="inline-flex items-center gap-2 bg-[#FC4C02] text-white px-4 py-2 rounded font-semibold hover:bg-[#E34402] transition-colors"
    >
      <ExternalLink size={18} />
      Connect with Strava
    </a>
  );
}
