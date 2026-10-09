export default function Loading() {
  return (
    <div className="flex animate-pulse flex-col gap-5" aria-busy="true" aria-label="Loading">
      <div className="h-11 w-72 rounded-lg bg-line-2" />
      <div className="flex flex-wrap gap-5">
        <div className="h-72 flex-[1.7_1_600px] rounded-[14px] bg-line-2" />
        <div className="h-72 flex-[1_1_360px] rounded-[14px] bg-line-2" />
      </div>
      <div className="h-36 rounded-[14px] bg-line-2" />
    </div>
  );
}
