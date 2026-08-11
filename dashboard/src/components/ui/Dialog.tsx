import type { ReactNode } from "react";
import { Dialog as HeadlessDialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";

export function Dialog({ open, onClose, title, description, children, actions }: { open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <HeadlessDialog open={open} onClose={onClose} className="relative z-50">
      <DialogBackdrop transition className="fixed inset-0 bg-black/30 duration-150 data-closed:opacity-0" />
      <div className="fixed inset-0 overflow-y-auto p-4"><div className="flex min-h-full items-center justify-center"><DialogPanel transition className="w-full max-w-lg rounded-lg border border-[#D9D9D6] bg-white shadow-xl duration-150 data-closed:scale-95 data-closed:opacity-0"><div className="border-b border-[#E5E5E2] p-5"><DialogTitle className="text-base font-semibold text-[#1D1D1F]">{title}</DialogTitle>{description ? <p className="mt-1 text-sm text-[#6B7280]">{description}</p> : null}</div><div className="p-5">{children}</div>{actions ? <div className="flex justify-end gap-2 border-t border-[#E5E5E2] bg-[#FAFAF9] p-4">{actions}</div> : null}</DialogPanel></div></div>
    </HeadlessDialog>
  );
}
