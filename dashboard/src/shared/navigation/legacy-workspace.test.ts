import { expect,it } from "vitest";
import { legacyTaskDestination,legacyScheduleDestination } from "./legacy-workspace";
it("moves known release and AI filters to their owning workspace without broadening collection",() => {
 expect(legacyTaskDestination({task_type:"export_data",tag:["a","b"]})).toBe("/publish/tasks?task_type=export_data&tag=a&tag=b");
 expect(legacyTaskDestination({task_type:"update_disease_knowledge",search:"中文"})).toBe("/models/tasks?task_type=update_disease_knowledge&search=%E4%B8%AD%E6%96%87");
 expect(legacyTaskDestination({task:"uuid",task_type:"export_data"})).toBe("/collection/runs?task=uuid&task_type=export_data");
 expect(legacyTaskDestination({task_type:"export_data,crawl_data"})).toContain("/collection/runs?");
});
it("moves release schedule bookmarks while retaining empty and repeated parameters",() => {
 const url=new URL(legacyScheduleDestination({kind:"release",tag:["a&b","中文"],empty:""}),"http://localhost");
 expect(url.pathname).toBe("/publish/schedules");expect(url.searchParams.getAll("tag")).toEqual(["a&b","中文"]);expect(url.searchParams.has("empty")).toBe(true);
 expect(legacyScheduleDestination({kind:"literature"})).toBe("/collection/schedules?kind=literature");
});
