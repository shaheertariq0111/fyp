import type { Metadata } from "next";
import { Poppins } from "next/font/google";
import type { ReactNode } from "react";
import "./styles.css";

const poppins = Poppins({
  display: "swap",
  subsets: ["latin"],
  variable: "--font-poppins",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Pizza Ordering Agent",
  description: "Chat and menu ordering interface",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html className={poppins.variable} lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
