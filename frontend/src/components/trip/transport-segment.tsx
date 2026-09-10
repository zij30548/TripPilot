import type { Transport } from "@/types/trip";

const modes = { walking: "步行", metro: "地铁", taxi: "出租车" };

export default function TransportSegment({ segment }: { segment: Transport }) {
  return (
    <div className="my-2 ml-3 border-l-2 border-dashed border-[#b9c9bf] py-4 pl-5 sm:ml-6" aria-label="活动间交通">
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-sm font-medium text-[#315f51]">
        <span>{modes[segment.mode]}</span><span>{segment.duration_minutes} 分钟</span><span>¥{segment.estimated_cost.toFixed(2)}</span><span className="text-xs leading-5 text-[#747b77]">Mock</span>
      </p>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">{segment.description}</p>
    </div>
  );
}
