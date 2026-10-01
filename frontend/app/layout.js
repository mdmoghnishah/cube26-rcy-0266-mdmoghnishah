import "./globals.css";

export const metadata = {
  title: "Recovery Manager",
  description: "Review financial charges against operational evidence",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}