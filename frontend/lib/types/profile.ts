export interface UserProfile {
  user_id: string;
  goal_type: string;
  target_date?: string;
  experience_level: string;
  weekly_days_available: number;
  current_weekly_km?: number;
  max_hr?: number;
  upcoming_races: { name: string; date: string; distance_km: number }[];
  injury_notes?: string;
  updated_at: string;
}
