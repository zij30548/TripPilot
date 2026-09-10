"use client";

import { useRef, useState, type FormEvent } from "react";
import { planTrip } from "@/lib/api";
import type { Pace, TripPlan, TripRequest } from "@/types/trip";

const paces = [
  { value: "relaxed", label: "轻松", description: "少走一点，慢慢感受" },
  { value: "balanced", label: "均衡", description: "张弛有度，经典与探索兼顾" },
  { value: "packed", label: "紧凑", description: "充分利用时间，多看几处" },
] as const;

const interestOptions = [
  "摄影",
  "Citywalk",
  "美食",
  "建筑",
  "博物馆",
  "购物",
] as const;

type SubmissionState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string };

type FormState = {
  startDate: string;
  endDate: string;
  totalBudget: string;
  travelers: string;
  accommodation: string;
  pace: Pace;
  interests: string[];
  mustVisitPlaces: string;
  excludedPlaces: string;
  earliestStartTime: string;
  latestEndTime: string;
};

const initialFormState: FormState = {
  startDate: "",
  endDate: "",
  totalBudget: "",
  travelers: "1",
  accommodation: "",
  pace: "balanced",
  interests: [],
  mustVisitPlaces: "",
  excludedPlaces: "",
  earliestStartTime: "09:00",
  latestEndTime: "21:00",
};

const inputClassName =
  "mt-2 w-full rounded-xl border border-[#d8d7d0] bg-[#fbfaf7] px-4 py-3 text-[15px] text-[#18201d] outline-none transition placeholder:text-[#a2a5a1] hover:border-[#aeb8b3] focus:border-[#315f51] focus:ring-3 focus:ring-[#315f51]/10";

function addDays(dateValue: string, days: number) {
  if (!dateValue) return undefined;

  const date = new Date(`${dateValue}T00:00:00`);
  date.setDate(date.getDate() + days);
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function getTripDays(startDate: string, endDate: string) {
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1;
}

function splitPlaces(value: string) {
  return value
    .split(/[，,\n]/)
    .map((place) => place.trim())
    .filter(Boolean);
}

export default function TripRequestForm({ onSuccess }: { onSuccess: (plan: TripPlan) => void }) {
  const [form, setForm] = useState<FormState>(initialFormState);
  const [dateError, setDateError] = useState("");
  const [timeError, setTimeError] = useState("");
  const [submission, setSubmission] = useState<SubmissionState>({ status: "idle" });
  const submitting = useRef(false);
  const isLoading = submission.status === "loading";

  const updateField = <Key extends keyof FormState>(
    field: Key,
    value: FormState[Key],
  ) => {
    setForm((current) => ({ ...current, [field]: value }));
    setSubmission({ status: "idle" });
  };

  const toggleInterest = (interest: string) => {
    const nextInterests = form.interests.includes(interest)
      ? form.interests.filter((item) => item !== interest)
      : [...form.interests, interest];

    updateField("interests", nextInterests);
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting.current) return;

    const tripDays = getTripDays(form.startDate, form.endDate);
    const nextDateError =
      !Number.isFinite(tripDays) || tripDays < 1 || tripDays > 3
        ? "目前只支持 1～3 日行程，请调整日期。" : "";
    const nextTimeError =
      form.earliestStartTime >= form.latestEndTime
        ? "最晚结束时间需要晚于最早出发时间。"
        : "";

    setDateError(nextDateError);
    setTimeError(nextTimeError);

    if (nextDateError || nextTimeError) return;

    const tripRequest: TripRequest = {
      start_date: form.startDate,
      end_date: form.endDate,
      budget: Number(form.totalBudget),
      travelers: Number(form.travelers),
      accommodation_location: form.accommodation.trim(),
      pace: form.pace,
      interests: form.interests,
      must_visit: splitPlaces(form.mustVisitPlaces),
      avoid_places: splitPlaces(form.excludedPlaces),
      daily_start_time: form.earliestStartTime,
      daily_end_time: form.latestEndTime,
    };

    submitting.current = true;
    setSubmission({ status: "loading" });
    try {
      const plan = await planTrip(tripRequest);
      setSubmission({ status: "idle" });
      onSuccess(plan);
    } catch (error) {
      setSubmission({
        status: "error",
        message: error instanceof Error ? error.message : "获取行程失败，请重试。",
      });
    } finally {
      submitting.current = false;
    }
  };

  return (
    <div className="min-w-0">
    <section
      aria-labelledby="trip-form-title"
      className="relative rounded-[1.75rem] border border-[#18201d]/8 bg-white p-5 shadow-[0_24px_70px_rgba(36,48,43,0.09)] sm:p-8 lg:p-10"
    >
      <div className="mb-8 flex items-start justify-between gap-4 border-b border-[#18201d]/8 pb-7">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] text-[#bd4c35] uppercase">
            Trip request
          </p>
          <h2
            id="trip-form-title"
            className="mt-2 text-2xl font-semibold tracking-[-0.035em] sm:text-3xl"
          >
            从你的旅行需求开始
          </h2>
          <p className="mt-2 text-sm leading-6 text-[#727975]">
            填写大约需要 2 分钟，带 * 的项目为必填项。
          </p>
        </div>
        <span className="hidden rounded-full bg-[#edf3ef] px-3 py-1.5 text-xs font-semibold text-[#315f51] sm:block">
          上海 · 1～3 天
        </span>
      </div>

      <form className="space-y-9" onSubmit={handleSubmit} aria-busy={isLoading}>
        <fieldset disabled={isLoading}>
          <legend className="mb-5 flex items-center gap-3 text-sm font-bold text-[#18392f]">
            <span className="grid size-7 place-items-center rounded-full bg-[#18392f] text-xs text-white">
              1
            </span>
            基本信息
          </legend>

          <div className="grid gap-5 sm:grid-cols-2">
            <label className="text-sm font-medium" htmlFor="start-date">
              开始日期 <span className="text-[#bd4c35]">*</span>
              <input
                className={inputClassName}
                id="start-date"
                name="startDate"
                type="date"
                required
                value={form.startDate}
                onChange={(event) => {
                  updateField("startDate", event.target.value);
                  setDateError("");
                }}
              />
            </label>

            <label className="text-sm font-medium" htmlFor="end-date">
              结束日期 <span className="text-[#bd4c35]">*</span>
              <input
                aria-describedby={dateError ? "date-error" : undefined}
                aria-invalid={Boolean(dateError)}
                className={inputClassName}
                id="end-date"
                max={addDays(form.startDate, 2)}
                min={form.startDate || undefined}
                name="endDate"
                type="date"
                required
                value={form.endDate}
                onChange={(event) => {
                  updateField("endDate", event.target.value);
                  setDateError("");
                }}
              />
            </label>
          </div>
          {dateError ? (
            <p id="date-error" className="mt-2 text-sm text-[#b3422d]" role="alert">
              {dateError}
            </p>
          ) : null}

          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <label className="text-sm font-medium" htmlFor="budget">
              总预算 <span className="text-[#bd4c35]">*</span>
              <div className="relative">
                <span className="pointer-events-none absolute top-1/2 left-4 mt-1 -translate-y-1/2 text-sm text-[#777e79]">
                  ¥
                </span>
                <input
                  className={`${inputClassName} pl-8`}
                  id="budget"
                  inputMode="decimal"
                  min="1"
                  name="totalBudget"
                  placeholder="例如 3000"
                  required
                  step="1"
                  type="number"
                  value={form.totalBudget}
                  onChange={(event) =>
                    updateField("totalBudget", event.target.value)
                  }
                />
              </div>
            </label>

            <label className="text-sm font-medium" htmlFor="travelers">
              旅行人数 <span className="text-[#bd4c35]">*</span>
              <div className="relative">
                <input
                  className={`${inputClassName} pr-12`}
                  id="travelers"
                  inputMode="numeric"
                  min="1"
                  name="travelers"
                  required
                  step="1"
                  type="number"
                  value={form.travelers}
                  onChange={(event) =>
                    updateField("travelers", event.target.value)
                  }
                />
                <span className="pointer-events-none absolute top-1/2 right-4 mt-1 -translate-y-1/2 text-sm text-[#777e79]">
                  人
                </span>
              </div>
            </label>
          </div>

          <label
            className="mt-5 block text-sm font-medium"
            htmlFor="accommodation"
          >
            住宿位置 <span className="text-[#bd4c35]">*</span>
            <input
              autoComplete="street-address"
              className={inputClassName}
              id="accommodation"
              name="accommodation"
              placeholder="例如：静安寺附近、南京东路某酒店"
              required
              type="text"
              value={form.accommodation}
              onChange={(event) =>
                updateField("accommodation", event.target.value)
              }
            />
            <span className="mt-2 block text-xs leading-5 font-normal text-[#858a87]">
              填写大致区域或酒店名称即可。
            </span>
          </label>
        </fieldset>

        <fieldset disabled={isLoading} className="border-t border-[#18201d]/8 pt-8">
          <legend className="mb-5 flex items-center gap-3 text-sm font-bold text-[#18392f]">
            <span className="grid size-7 place-items-center rounded-full bg-[#18392f] text-xs text-white">
              2
            </span>
            节奏与兴趣
          </legend>

          <div>
            <p className="text-sm font-medium">
              旅行节奏 <span className="text-[#bd4c35]">*</span>
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              {paces.map((pace) => (
                <label
                  key={pace.value}
                  className={`cursor-pointer rounded-xl border p-4 transition focus-within:ring-3 focus-within:ring-[#315f51]/10 ${
                    form.pace === pace.value
                      ? "border-[#315f51] bg-[#edf3ef]"
                      : "border-[#d8d7d0] bg-[#fbfaf7] hover:border-[#aeb8b3]"
                  }`}
                >
                  <input
                    className="sr-only"
                    checked={form.pace === pace.value}
                    name="pace"
                    required
                    type="radio"
                    value={pace.value}
                    onChange={() => updateField("pace", pace.value)}
                  />
                  <span className="flex items-center gap-2 text-sm font-semibold">
                    <span
                      aria-hidden="true"
                      className={`size-2 rounded-full ${
                        form.pace === pace.value
                          ? "bg-[#bd4c35]"
                          : "bg-[#c3c7c4]"
                      }`}
                    />
                    {pace.label}
                  </span>
                  <span className="mt-2 block text-xs leading-5 text-[#747b77]">
                    {pace.description}
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="mt-6">
            <p className="text-sm font-medium">兴趣偏好</p>
            <p className="mt-1 text-xs text-[#858a87]">可多选，也可以暂时不选。</p>
            <div className="mt-3 flex flex-wrap gap-2.5">
              {interestOptions.map((interest) => {
                const isSelected = form.interests.includes(interest);

                return (
                  <label
                    key={interest}
                    className={`cursor-pointer rounded-full border px-4 py-2.5 text-sm font-medium transition focus-within:ring-3 focus-within:ring-[#315f51]/10 ${
                      isSelected
                        ? "border-[#315f51] bg-[#18392f] text-white"
                        : "border-[#d8d7d0] bg-[#fbfaf7] text-[#525b57] hover:border-[#aeb8b3]"
                    }`}
                  >
                    <input
                      className="sr-only"
                      checked={isSelected}
                      name="interests"
                      type="checkbox"
                      value={interest}
                      onChange={() => toggleInterest(interest)}
                    />
                    <span aria-hidden="true" className="mr-1.5">
                      {isSelected ? "✓" : "+"}
                    </span>
                    {interest}
                  </label>
                );
              })}
            </div>
          </div>
        </fieldset>

        <fieldset disabled={isLoading} className="border-t border-[#18201d]/8 pt-8">
          <legend className="mb-5 flex items-center gap-3 text-sm font-bold text-[#18392f]">
            <span className="grid size-7 place-items-center rounded-full bg-[#18392f] text-xs text-white">
              3
            </span>
            地点与时间
          </legend>

          <div className="grid gap-5 sm:grid-cols-2">
            <label className="text-sm font-medium" htmlFor="must-visit">
              必去地点
              <textarea
                className={`${inputClassName} min-h-28 resize-y`}
                id="must-visit"
                name="mustVisitPlaces"
                placeholder="例如：外滩、武康路、上海博物馆"
                value={form.mustVisitPlaces}
                onChange={(event) =>
                  updateField("mustVisitPlaces", event.target.value)
                }
              />
              <span className="mt-2 block text-xs leading-5 font-normal text-[#858a87]">
                多个地点可用逗号或换行分隔。
              </span>
            </label>

            <label className="text-sm font-medium" htmlFor="excluded-places">
              不想去的地点
              <textarea
                className={`${inputClassName} min-h-28 resize-y`}
                id="excluded-places"
                name="excludedPlaces"
                placeholder="例如：大型商场、热门排队景点"
                value={form.excludedPlaces}
                onChange={(event) =>
                  updateField("excludedPlaces", event.target.value)
                }
              />
              <span className="mt-2 block text-xs leading-5 font-normal text-[#858a87]">
                地点或类型都可以填写。
              </span>
            </label>
          </div>

          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <label className="text-sm font-medium" htmlFor="earliest-start">
              每天最早出发时间 <span className="text-[#bd4c35]">*</span>
              <input
                aria-describedby={timeError ? "time-error" : undefined}
                aria-invalid={Boolean(timeError)}
                className={inputClassName}
                id="earliest-start"
                name="earliestStartTime"
                required
                type="time"
                value={form.earliestStartTime}
                onChange={(event) => {
                  updateField("earliestStartTime", event.target.value);
                  setTimeError("");
                }}
              />
            </label>

            <label className="text-sm font-medium" htmlFor="latest-end">
              每天最晚结束时间 <span className="text-[#bd4c35]">*</span>
              <input
                aria-describedby={timeError ? "time-error" : undefined}
                aria-invalid={Boolean(timeError)}
                className={inputClassName}
                id="latest-end"
                name="latestEndTime"
                required
                type="time"
                value={form.latestEndTime}
                onChange={(event) => {
                  updateField("latestEndTime", event.target.value);
                  setTimeError("");
                }}
              />
            </label>
          </div>
          {timeError ? (
            <p id="time-error" className="mt-2 text-sm text-[#b3422d]" role="alert">
              {timeError}
            </p>
          ) : null}
        </fieldset>

        <div className="border-t border-[#18201d]/8 pt-7">
          {submission.status === "error" && (
            <p role="alert" className="mb-4 rounded-xl bg-red-50 p-3 text-sm text-[#b3422d]">
              {submission.message}
            </p>
          )}
          {isLoading && (
            <p role="status" className="mb-4 text-sm text-[#315f51]">正在获取行程，请稍候…</p>
          )}
          <button
            className="group flex w-full items-center justify-center gap-3 rounded-xl bg-[#df5b3f] px-6 py-4 text-base font-semibold text-white shadow-[0_12px_28px_rgba(189,76,53,0.22)] transition hover:bg-[#c94e35] focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-[#18392f] active:translate-y-px disabled:cursor-wait disabled:opacity-60"
            disabled={isLoading}
            type="submit"
          >
            {isLoading ? "正在获取行程…" : "生成我的行程"}
            <span
              aria-hidden="true"
              className="transition-transform group-hover:translate-x-1"
            >
              →
            </span>
          </button>
          <p className="mt-3 text-center text-xs leading-5 text-[#8a8f8c]">
            当前返回固定上海两日 Mock 示例，暂不按你的需求调整行程。
          </p>
        </div>
      </form>
    </section>
    </div>
  );
}
