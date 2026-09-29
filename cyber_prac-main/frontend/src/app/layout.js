import { Inter, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";
import ThemeScript from "./theme-script";
import ThemeProvider from "@/components/ui/ThemeProvider";

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
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FAF8FB" },
    { media: "(prefers-color-scheme: dark)", color: "#05030A" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${jakarta.variable} h-full antialiased`}
    >
      <head>
        <ThemeScript />
      </head>
      <body className="h-full font-sans">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
