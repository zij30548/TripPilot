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
  name: string;
  description: string;
  start_time: string;
  end_time: string;
};

export type DayPlan = {
  day: number;
  title: string;
  activities: Activity[];
};

export type TripPlan = {
  destination: string;
  estimated_cost: number;
  currency: "CNY";
  is_mock: true;
  notice: string;
  days: DayPlan[];
};
