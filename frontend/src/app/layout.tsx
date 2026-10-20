import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { AuthenticationBoundary } from "@/components/auth/authentication-boundary";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "TripPilot｜先聊清楚，再出发",
  description: "通过多轮表达与明确确认，整理你的旅行需求。地点与行程仍需真实数据核对，模型不会自动预订或执行。",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full"><AuthenticationBoundary>{children}</AuthenticationBoundary></body>
    </html>
  );
}
