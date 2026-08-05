import { FileText, CheckCircle2, AlertTriangle, XCircle, RefreshCw } from 'lucide-react';
import { DashboardStats } from '@/lib/api';

interface StatsOverviewProps {
  stats: DashboardStats | null;
}

export default function StatsOverview({ stats }: StatsOverviewProps) {
  const cards = [
    {
      title: 'Total Documents',
      value: stats?.total_documents ?? 0,
      icon: FileText,
      color: 'text-blue-400 bg-blue-500/10 border-blue-500/20',
    },
    {
      title: 'Completed',
      value: stats?.completed ?? 0,
      icon: CheckCircle2,
      color: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
    },
    {
      title: 'Needs Review',
      value: stats?.needs_review ?? 0,
      icon: AlertTriangle,
      color: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
    },
    {
      title: 'Failed',
      value: stats?.failed ?? 0,
      icon: XCircle,
      color: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
    },
  ];

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((card, idx) => {
        const Icon = card.icon;
        return (
          <div
            key={idx}
            className="bg-[#111827] border border-gray-800 p-5 rounded-xl flex items-center justify-between shadow-lg"
          >
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-400">
                {card.title}
              </p>
              <h4 className="text-2xl font-extrabold text-gray-100 mt-1">
                {card.value}
              </h4>
            </div>
            <div className={`p-3 rounded-lg border ${card.color}`}>
              <Icon className="w-6 h-6" />
            </div>
          </div>
        );
      })}
    </div>
  );
}
