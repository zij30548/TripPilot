"use client";

import { useState, type FormEvent } from "react";

export default function PlaceSearch({ loading, onSearch }: {
  loading: boolean;
  onSearch: (keyword: string) => void;
}) {
  const [keyword, setKeyword] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSearch(keyword);
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <label htmlFor="place-keyword" className="block text-sm font-medium text-[#315f51]">搜索上海地点</label>
      <div className="flex gap-2">
        <input id="place-keyword" value={keyword} onChange={(event) => setKeyword(event.target.value)}
          placeholder="输入地点名称" autoComplete="off"
          className="min-w-0 flex-1 rounded-xl border border-[#d8d7d0] bg-[#fbfaf7] px-3 py-2.5 text-sm outline-none focus:border-[#315f51] focus:ring-2 focus:ring-[#315f51]/15" />
        <button type="submit" aria-label="搜索" className="shrink-0 rounded-xl bg-[#18392f] px-4 py-2.5 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#315f51]">
          {loading ? "搜索中…" : "搜索"}
        </button>
      </div>
      <p className="text-xs leading-5 text-[#68726c]">范围固定为上海。输入关键词后点击搜索。</p>
    </form>
  );
}
