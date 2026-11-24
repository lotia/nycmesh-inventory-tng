import { ScanPage } from "./scan/ScanPage";
import { mount } from "./shared/mount";
import { takeSeparatorFrom } from "./shared/quantity";
import { telemetry } from "./shared/telemetry";

telemetry.start("scan");
takeSeparatorFrom(document.documentElement);

mount(<ScanPage reload={() => location.reload()} />);
