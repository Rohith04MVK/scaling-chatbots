"use client";

export default function LoadingDots() {
  return (
    <div className="flex gap-1.5 px-[18px] py-3.5 mb-5">
      <div
        className="w-1.5 h-1.5 bg-accent"
        style={{ animation: "pulse-dot 1s ease-in-out infinite" }}
      />
      <div
        className="w-1.5 h-1.5 bg-accent"
        style={{ animation: "pulse-dot 1s ease-in-out 0.2s infinite" }}
      />
      <div
        className="w-1.5 h-1.5 bg-accent"
        style={{ animation: "pulse-dot 1s ease-in-out 0.4s infinite" }}
      />
    </div>
  );
}
