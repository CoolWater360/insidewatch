import { Sidebar } from "@/components/internal/Sidebar";
import { TopBar } from "@/components/internal/TopBar";

export const metadata = { title: "InsideWatch — Institutional Intelligence Console" };

export default function InternalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex overflow-hidden bg-navy-950 text-[#E8EDF7]">
      <Sidebar />
      {/* Content area — offset by sidebar width on lg+ */}
      <div className="flex flex-1 flex-col overflow-hidden lg:pl-52">
        <TopBar />
        <main className="flex-1 overflow-y-auto">
          <div className="px-6 py-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
