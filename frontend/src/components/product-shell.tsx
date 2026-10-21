"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode } from "react";
import ConversationWorkspace from "./chat/conversation-workspace";
import KnowledgeSearch from "./knowledge-search";
import EvaluationWorkspace from "./evaluation-workspace";

const pages = [{ href: "/", name: "对话助手", mark: "chat" }, { href: "/knowledge", name: "知识库", mark: "book" }, { href: "/evaluation", name: "Agent 评测", mark: "chart" }] as const;
function Mark({ name }: { name: string }) {
  return <svg className="size-4 shrink-0" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.35" aria-hidden="true">{name === "chat" ? <path d="M16 3.5H4a1.5 1.5 0 0 0-1.5 1.5v8A1.5 1.5 0 0 0 4 14.5h3l3 3v-3h6a1.5 1.5 0 0 0 1.5-1.5V5A1.5 1.5 0 0 0 16 3.5Z" /> : name === "book" ? <path d="M10 4.5C7.5 3 5 3 2.5 4v12C5 15 7.5 15 10 16.5m0-12C12.5 3 15 3 17.5 4v12C15 15 12.5 15 10 16.5m0-12v12" /> : <path d="M3 3v14h14M6 13V9m4 4V5m4 8V7" />}</svg>;
}

export default function ProductShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const supported = pages.some(page => page.href === pathname);
  // One stable instance per authenticated identity. Navigation changes visibility,
  // not the controller: unsent text and in-flight cancellation IDs stay intact.
  if (!supported) return children;
  return <div className="min-h-dvh bg-white text-[#17211f]">
    <header className="border-b border-[#e8ece9] bg-white px-5 md:px-8">
      <div className="relative mx-auto flex min-h-[114px] max-w-[1600px] flex-col justify-between pb-2 pt-5 md:min-h-[76px] md:flex-row md:items-center md:justify-start md:gap-16 md:py-0">
        <Link href="/" prefetch={false} className="inline-flex w-fit items-center gap-2.5 text-lg font-semibold tracking-[-0.05em] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#294e43]" aria-label="TripPilot 首页"><span className="grid size-7 place-items-center rounded-lg bg-[#294e43] text-base font-medium text-white" aria-hidden="true">t</span>TripPilot</Link>
        <nav aria-label="主导航" className="flex gap-1.5 md:gap-3">{pages.map(page => <Link key={page.href} href={page.href} prefetch={false} aria-current={pathname === page.href ? "page" : undefined} className={`inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-xs transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#294e43] ${pathname === page.href ? "bg-[#edf3ef] font-medium text-[#294e43]" : "text-[#8a938e] hover:bg-[#f7f8fa] hover:text-[#294e43]"}`}><Mark name={page.mark} />{page.name}</Link>)}</nav>
      </div>
    </header>
    <div hidden={pathname !== "/"} inert={pathname !== "/"}><ConversationWorkspace /></div>
    <div hidden={pathname !== "/knowledge"} inert={pathname !== "/knowledge"}><KnowledgeSearch /></div>
    <div hidden={pathname !== "/evaluation"} inert={pathname !== "/evaluation"}><EvaluationWorkspace active={pathname === "/evaluation"} /></div>
  </div>;
}
