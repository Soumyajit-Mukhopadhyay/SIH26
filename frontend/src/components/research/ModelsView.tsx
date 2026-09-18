import { clsx } from 'clsx';

import type { ModelStatus, ResearchDataset } from './types';
import { ColLabel, Dot, KV, Lede, Notice, Tag, Title, Working } from './ui';

/**
 * The model registry. One entry today; the table is still a table because a
 * registry with one row reads as "the first of several" and a card with one
 * model reads as a brochure. Every value is the /ml/models response verbatim —
 * readiness, skill and baseline are shown only when the backend reports them.
 */
export function ModelsView({
  models,
  policy,
  catalogueEntry,
}: {
  models: ModelStatus[] | null;
  policy: string | null;
  catalogueEntry?: ResearchDataset;
}) {
  return (
    <div className="mx-auto w-full max-w-[1440px] px-6 py-6 lg:px-10 lg:py-8">
      <Title>Models</Title>
      <Lede>
        Learned components ORCA ships, with their status on this deployment. A model&rsquo;s output
        is evidence for a researcher, never an input to a safety verdict.
      </Lede>

      {models === null ? (
        <div className="mt-8">
          <Working label="Asking the backend which models are loaded…" />
        </div>
      ) : (
        <>
          <table className="mt-8 w-full text-xs">
            <thead>
              <tr className="border-hairline-strong border-b">
                <th className="label py-1.5 pr-4 text-left font-medium">Model</th>
                <th className="label py-1.5 pr-4 text-left font-medium">Version</th>
                <th className="label py-1.5 pr-4 text-left font-medium">Status</th>
                <th className="label hidden py-1.5 pr-4 text-left font-medium md:table-cell">
                  Input
                </th>
                <th className="label hidden py-1.5 pr-4 text-left font-medium md:table-cell">
                  Leads
                </th>
                <th className="label hidden py-1.5 text-left font-medium lg:table-cell">
                  Task
                </th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.version} className="border-hairline border-b">
                  <td className="text-ink-0 py-2.5 pr-4 text-[13px] font-medium">FrontCast</td>
                  <td className="data text-ink-1 py-2.5 pr-4">{m.version}</td>
                  <td className="py-2.5 pr-4">
                    <StatusTag status={m} />
                  </td>
                  <td className="data text-ink-1 hidden py-2.5 pr-4 md:table-cell">
                    {m.history_days} d SST
                  </td>
                  <td className="data text-ink-1 hidden py-2.5 pr-4 md:table-cell">
                    {m.lead_days.map((d) => `+${d}`).join(' / ')} d
                  </td>
                  <td className="text-ink-1 hidden py-2.5 lg:table-cell">
                    Thermal-front position forecast
                  </td>
                </tr>
              ))}
              {!models.length ? (
                <tr>
                  <td colSpan={6} className="text-ink-1 py-3">
                    The backend reports no models.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>

          {models.map((m) => (
            <ModelDetail key={m.version} status={m} catalogueEntry={catalogueEntry} />
          ))}

          {policy ? (
            <p className="text-ink-2 mt-10 max-w-3xl text-xs leading-relaxed">{policy}</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function StatusTag({ status }: { status: ModelStatus }) {
  if (status.ready) return <Tag tone="cyan">Ready</Tag>;
  if (!status.torch_installed) return <Tag tone="amber">Unavailable · no PyTorch</Tag>;
  if (!status.weights_present) return <Tag tone="amber">Unavailable · no weights</Tag>;
  return <Tag tone="amber">Unavailable</Tag>;
}

function Check({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) {
  return (
    <div className="flex items-start gap-2.5 py-2">
      <Dot tone={ok ? 'cyan' : 'amber'} className="mt-[5px]" />
      <div className="min-w-0">
        <div className="text-ink-0 text-xs">
          {label} <span className={clsx('ml-1', ok ? 'text-ink-1' : 'text-amber')}>{ok ? 'yes' : 'no'}</span>
        </div>
        {detail ? <div className="data text-ink-2 truncate text-[11px]">{detail}</div> : null}
      </div>
    </div>
  );
}

function ModelDetail({
  status,
  catalogueEntry,
}: {
  status: ModelStatus;
  catalogueEntry?: ResearchDataset;
}) {
  const report = status.training_report;
  const leads = Object.keys(report?.metrics ?? {});
  const unavailableWhy =
    status.load_error ??
    (!status.torch_installed
      ? 'PyTorch is not installed on this deployment, so the model cannot run here. Nothing in the safety path depends on it.'
      : !status.weights_present
        ? 'The trained weights are not present on this deployment.'
        : null);

  return (
    <div className="mt-10 grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      {/* ------------------------------------------------ availability */}
      <div>
        <ColLabel>Availability on this deployment</ColLabel>
        <div className="divide-hairline mt-1 divide-y">
          <Check ok={status.torch_installed} label="PyTorch installed" />
          <Check
            ok={status.weights_present}
            label="Trained weights present"
            detail={status.weights_path}
          />
          <Check ok={status.ready} label="Ready for inference" />
        </div>
        {!status.ready && unavailableWhy ? (
          <Notice tone="amber" className="mt-4">
            {unavailableWhy}
          </Notice>
        ) : null}

        <ColLabel className="mt-8">Specification</ColLabel>
        <KV
          className="mt-2"
          rows={[
            {
              k: 'Input',
              v: (
                <span className="data">
                  {status.history_days} days of SST
                  {status.input_channels?.length ? (
                    <span className="text-ink-2"> · {status.input_channels.join(', ')}</span>
                  ) : null}
                </span>
              ),
            },
            {
              k: 'Output',
              v: (
                <span className="data">
                  front probability at {status.lead_days.map((d) => `+${d}`).join(', ')} d
                </span>
              ),
            },
            {
              k: 'Architecture',
              v: 'CNN encoder per day → transformer over the day axis → one sigmoid head per lead (independent, so a persistent front is representable)',
            },
            ...(catalogueEntry
              ? [
                  { k: 'Grid', v: catalogueEntry.native_resolution },
                  { k: 'Coverage', v: catalogueEntry.coverage },
                  {
                    k: 'Endpoint',
                    v: <span className="data">{catalogueEntry.endpoint}</span>,
                  },
                ]
              : []),
          ]}
        />
      </div>

      {/* ------------------------------------------------------- skill */}
      <div>
        <ColLabel>Measured skill</ColLabel>
        {leads.length ? (
          <>
            <table className="mt-2 w-full text-xs">
              <thead>
                <tr className="border-hairline-strong border-b">
                  <th className="label py-1.5 text-left font-medium">Lead</th>
                  <th className="label py-1.5 text-right font-medium">Model F1</th>
                  <th className="label py-1.5 text-right font-medium">Model IoU</th>
                  <th className="label py-1.5 text-right font-medium">Persistence F1</th>
                  <th className="label py-1.5 pl-4 text-left font-medium">Against baseline</th>
                </tr>
              </thead>
              <tbody className="data">
                {leads.map((lead) => {
                  const m = report?.metrics?.[lead] ?? {};
                  const b = report?.baseline?.[lead] ?? {};
                  const beats = (m.f1 ?? 0) > (b.f1 ?? 0);
                  return (
                    <tr key={lead} className="border-hairline border-b">
                      <td className="text-ink-0 py-2">{lead}</td>
                      <td className="text-ink-0 py-2 text-right">{(m.f1 ?? 0).toFixed(3)}</td>
                      <td className="text-ink-1 py-2 text-right">{(m.iou ?? 0).toFixed(3)}</td>
                      <td className="text-ink-1 py-2 text-right">{(b.f1 ?? 0).toFixed(3)}</td>
                      <td className={clsx('py-2 pl-4', beats ? 'text-ink-0' : 'text-amber')}>
                        {beats ? 'beats persistence' : 'does not beat persistence'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="text-ink-2 mt-3 text-xs leading-relaxed">
              Persistence is the forecast &ldquo;tomorrow&rsquo;s fronts are today&rsquo;s&rdquo;.
              A skill score is only information beside the baseline it must beat.
            </p>
            {report?.trained_at || report?.samples_train ? (
              <KV
                dense
                className="mt-4"
                rows={[
                  ...(report.trained_at ? [{ k: 'Trained', v: <span className="data">{report.trained_at}</span> }] : []),
                  ...(report.samples_train
                    ? [
                        {
                          k: 'Samples',
                          v: (
                            <span className="data">
                              {report.samples_train} train
                              {report.samples_val ? ` · ${report.samples_val} val` : ''}
                            </span>
                          ),
                        },
                      ]
                    : []),
                  ...(report.epochs ? [{ k: 'Epochs', v: <span className="data">{report.epochs}</span> }] : []),
                ]}
              />
            ) : null}
            {report?.notes ? (
              <p className="text-ink-2 mt-3 text-xs leading-relaxed">{report.notes}</p>
            ) : null}
          </>
        ) : (
          <p className="text-ink-1 mt-2 text-xs leading-relaxed">
            No training report on this deployment, so no skill figures are shown. A model without
            a published score beside a persistence baseline should be read as untested here.
          </p>
        )}

        <ColLabel className="mt-8">Provenance and limits</ColLabel>
        <KV
          className="mt-2"
          rows={[
            { k: 'Labels', v: status.label_source.replace(' — ', ', ') },
            { k: 'Not a verdict', v: status.not_a_verdict },
          ]}
        />
      </div>
    </div>
  );
}
