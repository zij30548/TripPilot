export type Pace = "relaxed" | "balanced" | "packed";

export type TripRequest = {
  start_date: string;
  end_date: string;
  budget: number;
  travelers: number;
  accommodation_location: string;
  pace: Pace;
  interests: string[];
  must_visit: string[];
  avoid_places: string[];
  daily_start_time: string;
  daily_end_time: string;
};

export type Activity = {
  id: string;
  name: string;
  category: "sightseeing" | "food" | "museum" | "shopping";
  estimated_cost: number;
  description: string;
  start_time: string;
  end_time: string;
};

export type Transport = {
  from_activity_id: string;
  to_activity_id: string;
  mode: "walking" | "metro" | "taxi";
  duration_minutes: number;
  estimated_cost: number;
  description: string;
};

export type Weather = {
  date: string;
  condition: string;
  min_temperature: number;
  max_temperature: number;
  rain_risk: number;
};

export type BudgetBreakdown = {
  transport: number;
  food: number;
  tickets: number;
  other: number;
};

export type DayPlan = {
  day: number;
  date: string;
  title: string;
  activities: Activity[];
  transports: Transport[];
  weather: Weather;
};

export type TripPlan = {
  destination: string;
  request: TripRequest;
  budget_breakdown: BudgetBreakdown;
  estimated_cost: number;
  currency: "CNY";
  is_mock: true;
  notice: string;
  days: DayPlan[];
};
