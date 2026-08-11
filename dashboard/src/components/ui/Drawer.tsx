import type { ReactNode } from "react";
import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";
import { X } from "lucide-react";

export function Drawer({ open, onClose, title, description, children, size = "lg" }: { open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children: ReactNode; size?: "md" | "lg" | "full" }) {
  const width = size === "full" ? "max-w-[calc(100vw-1rem)]" : size === "md" ? "max-w-xl" : "max-w-3xl";
  return (
    <Dialog open={open} onClose={onClose} className="relative z-50"><DialogBackdrop transition className="fixed inset-0 bg-black/25 duration-200 data-closed:opacity-0" /><div className="fixed inset-0 flex justify-end p-2 sm:p-3"><DialogPanel transition className={`flex h-full w-full ${width} translate-x-0 flex-col rounded-lg border border-[#D9D9D6] bg-white shadow-2xl duration-200 data-closed:translate-x-full`}><div className="flex items-start gap-3 border-b border-[#E5E5E2] p-4"><div className="min-w-0 flex-1"><DialogTitle className="text-base font-semibold text-[#1D1D1F]">{title}</DialogTitle>{description ? <p className="mt-1 text-sm text-[#6B7280]">{description}</p> : null}</div><button type="button" onClick={onClose} aria-label="Close drawer" className="rounded-md p-1.5 text-[#6B7280] hover:bg-[#F7F7F5] hover:text-[#1D1D1F]"><X className="h-4 w-4" /></button></div><div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div></DialogPanel></div></Dialog>
  );
}
