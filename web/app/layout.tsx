import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "True AI Detective",
  description: "A private AI detective that investigates cases over time",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  );
}
