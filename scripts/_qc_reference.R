# Точна репродукція ключової QC-логіки qmd-скрипта (з тригран-NA),
# тільки base-R, без tidyverse/sf. Видає summary "Issue/Normal/Missing" по полях.

files <- list.files("/tmp/r_data", pattern = "\\.csv$", full.names = TRUE)
dfs <- lapply(files, function(f) {
  read.csv(f, stringsAsFactors = FALSE, na.strings = c("NA","","NaN"," "))
})
# узгоджуємо колонки (різні листи мають різні набори)
all_cols <- unique(unlist(lapply(dfs, colnames)))
for (i in seq_along(dfs)) {
  miss <- setdiff(all_cols, colnames(dfs[[i]]))
  for (m in miss) dfs[[i]][[m]] <- NA
  dfs[[i]] <- dfs[[i]][, all_cols, drop = FALSE]
}
d <- do.call(rbind, dfs)
cat("Total rows (R-ref):", nrow(d), "\n")
d <- d[!is.na(d$deployment_id) & d$deployment_id != "", ]
cat("After filter on deployment_id:", nrow(d), "\n")

# helpers: ensure logical columns
asL <- function(x) {
  if (is.logical(x)) return(x)
  out <- rep(NA, length(x))
  out[tolower(as.character(x)) %in% c("true","1","yes","y","x","так","+")] <- TRUE
  out[tolower(as.character(x)) %in% c("false","0","no","n","ні","-","none")] <- FALSE
  as.logical(out)
}
need <- c("qc_non_functional","qc_stolen","qc_hardware_issue","qc_firmware_issue",
          "qc_settings_issue","qc_battery_issue","qc_sd_issue","qc_no_data_uploaded_by_PA",
          "qc_uploaded_data_is_not_raw","qc_no_species_captured","qc_placement_incorrect",
          "qc_poor_placement","qc_feeding_location","qc_installation_incorrect",
          "qc_lapse_photos_missed","qc_installation_photos_missed","qc_deinstallation_photos_missed",
          "qc_distance_reference_photos_missed","qc_datetime_photos_missed",
          "qc_local_datetime_not_set","qc_data_not_usable")
for (c_ in need) {
  if (is.null(d[[c_]])) d[[c_]] <- NA
  d[[c_]] <- asL(d[[c_]])
}

d$latitude  <- suppressWarnings(as.numeric(d$latitude))
d$longitude <- suppressWarnings(as.numeric(d$longitude))
d$start_date <- as.Date(d$start_date)
d$end_date   <- as.Date(d$end_date)
d$n_days_working <- as.numeric(d$end_date - d$start_date)
d$qc_no_GPS_coordinates <- is.na(d$latitude) | is.na(d$longitude)

# Похідні (як в qmd, R-OR з NA-пропагацією)
d$qc_data_not_usable <- d$qc_data_not_usable |
                        d$qc_no_GPS_coordinates |
                        d$qc_feeding_location |
                        d$qc_hardware_issue |
                        (d$qc_installation_incorrect & d$qc_no_species_captured) |
                        (d$qc_placement_incorrect    & d$qc_no_species_captured) |
                        (d$qc_poor_placement         & d$qc_no_species_captured)

d$qc_summary <- d$qc_data_not_usable | d$qc_no_data_uploaded_by_PA |
                d$qc_sd_issue | d$qc_stolen | d$qc_non_functional

d$qc_min_days_not_reached <-
  (d$study_season == "Winter" & d$n_days_working < 100) |
  (d$study_season == "Summer" & d$n_days_working < 60)

fields <- c(need, "qc_no_GPS_coordinates","qc_summary","qc_min_days_not_reached")
cat("\nfield\tIssue\tNormal\tMissing\n")
for (f in fields) {
  v <- d[[f]]
  issue   <- sum(v == TRUE, na.rm = TRUE)
  normal  <- sum(v == FALSE, na.rm = TRUE)
  missing <- sum(is.na(v))
  cat(sprintf("%s\t%d\t%d\t%d\n", f, issue, normal, missing))
}
