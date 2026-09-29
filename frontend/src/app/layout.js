import { Inter, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

/* Body face — clean, neutral, high legibility in dense data UIs */
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});

/* Display face — geometric upright sans, used for headings and numerals */
const jakarta = Plus_Jakarta_Sans({
  variable: "--font-jakarta",
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
  display: "swap",
});

export const metadata = {
  title: "Cyber Assessment Agent",
  description: "Enterprise-grade security assessments powered by AI",
};

export const viewport = {
  themeColor: "#2563eb",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jakarta.variable} h-full antialiased`}
    >
      <body className="h-full bg-slate-50 text-slate-900 font-sans">{children}</body>
    </html>
  );
}
