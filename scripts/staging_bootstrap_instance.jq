# Resolve the portal database explicitly; Cloud SQL attachments are a list.
def one_env($name):
  [.env[]? | select(.name == $name)] as $entries
  | if ($entries | length) == 1 and ($entries[0] | keys | sort) == ["name", "value"]
    then $entries[0].value else error("ambiguous bootstrap database environment") end;
if $project != "sawahospitalsystem" or $region != "asia-northeast2" or $service != "web-stg"
then error("staging bootstrap context rejected") else . end
| "sawahospitalsystem:asia-northeast2:orders-stg" as $orders
| "sawahospitalsystem:asia-northeast2:school-lunch-stg" as $school
| .spec.template as $template
| ($template.metadata.annotations["run.googleapis.com/cloudsql-instances"] | split(",")) as $instances
| $template.spec.containers as $containers
| if ($instances | sort) != [$orders] and ($instances | sort) != ([$orders, $school] | sort)
  then error("staging bootstrap attachments rejected") else . end
| if ($instances | length) == 2 and ($containers | length) != 2
  then error("school attachment requires known sidecar") else . end
| if ($containers | length) != 1 and
    (($containers | length) != 2 or $containers[1].name != "agent-runtime" or (($containers[1].ports // []) | length) != 0 or
     (($containers[1].image // "") | test("^asia-northeast2-docker[.]pkg[.]dev/sawahospitalsystem/backend/agent-runtime@sha256:[0-9a-f]{64}$") | not))
  then error("staging bootstrap containers rejected") else . end
| $containers[0]
| if ((.ports // []) | length) == 0 or
     one_env("DB_HOST") != ("/cloudsql/" + $orders) or
     one_env("DB_NAME") != "orders" or one_env("DB_USER") != "orders_app"
  then error("staging bootstrap database target rejected") else $orders end
