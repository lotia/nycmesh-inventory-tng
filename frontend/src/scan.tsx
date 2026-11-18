import { ScanPage } from "./scan/ScanPage";
import { mount } from "./shared/mount";
import { telemetry } from "./shared/telemetry";

telemetry.start("scan");

mount(<ScanPage reload={() => location.reload()} />);
