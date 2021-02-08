import numpy
import cobra
import optlang.cplex_interface
import optlang.glpk_interface
from optlang.symbolics import add, mul
from optlang.exceptions import IndicatorConstraintsNotSupported
from swiglpk import glp_write_lp
#import cplex
from cplex.exceptions import CplexSolverError
from cplex._internal._subinterfaces import SolutionStatus # can be also accessed by a CPLEX object under .solution.status
import itertools

# exec(open('cMCS_enumerator.py').read())

class ConstrainedMinimalCutSetsEnumerator:
    def __init__(self, optlang_interface, st, reversible, targets, kn=None, cuts=None,
        desired= [], knock_in=[], bigM=0, threshold=1, split_reversible_v=True,
        reduce_constraints=True, combined_z=True, irrev_geq=False, ref_set= None):
        # targets is a list of (T,t) pairs that represent T <= t
        # combined_z will probably be fixed to True which implies reduce_constraints=True
        self.ref_set = ref_set # optional set of reference MCS for debugging
        self.model = optlang_interface.Model()
        self.model.configuration.presolve = True # presolve on
        # without presolve CPLEX sometimes gives false results when using indicators ?!?
        self.model.configuration.lp_method = 'auto'
        self.optlang_constraint_class = optlang_interface.Constraint
        if bigM <= 0 and self.optlang_constraint_class._INDICATOR_CONSTRAINT_SUPPORT is False:
            raise IndicatorConstraintsNotSupported("This solver does not support indicators. Please choose a differen solver or use a big M formulation.")
        self.optlang_variable_class = optlang_interface.Variable
        irr = [not r for r in reversible]
        self.num_reac = len(reversible)
        if cuts is None:
            #cuts = [True] * self.num_reac
            cuts = numpy.full(self.num_reac, True, dtype=bool)
            irrepressible = []
        else:
            irrepressible = numpy.where(cuts == False)[0]
            #print("irrepressible", irrepressible)
            #iv_cost(irrepressible)= 0;
        num_targets = len(targets)
        use_kn_in_dual = kn is not None
        if split_reversible_v:
            split_v_idx = [i for i, x in enumerate(reversible) if x]
            dual_rev_neg_idx = [i for i in range(self.num_reac, self.num_reac + len(split_v_idx))]
            dual_rev_neg_idx_map = [None] * self.num_reac
            for i in range(len(split_v_idx)):
                dual_rev_neg_idx_map[split_v_idx[i]]= dual_rev_neg_idx[i];
            #print(split_v_idx)
            #print(dual_rev_neg_idx)
            #print(dual_rev_neg_idx_map)
        else:
            split_v_idx = []

        self.zero_objective= optlang_interface.Objective(0, direction='min', name='zero_objective')
        self.model.objective= self.zero_objective;
        self.z_vars = [self.optlang_variable_class("Z"+str(i), type="binary", problem=self.model.problem) for i in range(self.num_reac)]
        self.model.add(self.z_vars)
        self.model.update() # cannot change bound below without this
        for i in irrepressible:
            self.z_vars[i].ub = 0 # nur wenn es keine KI sind
        self.minimize_sum_over_z= optlang_interface.Objective(add(self.z_vars), direction='min', name='minimize_sum_over_z')
        z_local = [None] * num_targets
        if num_targets == 1:
            #obj.z_var_names= cell(obj.num_reac + length(obj.rev_pos_idx), 1);
            #obj.z_var_names(1:obj.num_reac)= strcat(z_string, nums_idx(1:obj.num_reac));
            #obj.z_var_names(obj.rev_neg_idx)= strcat('ZN', nums_idx(obj.rev_pos_idx));
            #z_local = [self.z_vars] # global and local Z are the same if there is only one target
            z_local[0] = self.z_vars # global and local Z are the same if there is only one target
            #iv_cost= [iv_cost, iv_cost(obj.rev_pos_idx)];
        else:
            # da reduced_constraints tandard ist können auch hier lokale und globale Z gleich sein
            for k in range(num_targets):
                z_local[k] = [self.optlang_variable_class("Z"+str(k)+"_"+str(i), type="binary", problem=self.model.problem) for i in range(self.num_reac)]
                self.model.add(z_local[k])
            # z_local= cell(obj.num_reac + length(obj.rev_pos_idx), num_targets);
            # obj.z_var_names= strcat('Z', numsn); % global Z
            # obj.split_z= false;
            # for k= 1:num_targets
            # z_local(1:obj.num_reac, k)= strcat(sprintf('%s%d_', z_string, k), nums_idx(1:obj.num_reac));
            # z_local(obj.rev_neg_idx, k)= strcat(sprintf('ZN%d_', k), nums_idx(obj.rev_pos_idx));
            for i in range(self.num_reac):
                if cuts[i]: # && ~knock_in(i) % knock-ins only use global Z, do not need local ones
                    #if irr[i]: #|| combined_z
                    self.model.add(self.optlang_constraint_class(
                        (1/num_targets - 1e-9)*add([z_local[k][i] for k in range(num_targets)]) - self.z_vars[i], ub=0,
                        name= "ZL"+str(i)))

                    #lpfw.write_expression([], [repmat(1/num_targets - 1e-9, 1, num_targets), -1],...
                    #    [z_local(i, :), sprintf('%s', obj.z_var_names{i})], '<= 0');
                    #else:
                    #    lpfw.write_expression([], [repmat(1/(2*num_targets) - 1e-9, 1, 2*num_targets), -1],...
                    #    [z_local(i, :), z_local(obj.rev_neg_idx_map(i), :), sprintf('%s', obj.z_var_names{i})], '<= 0');


        dual_vars = [None] * num_targets
        num_dual_cols = [0] * num_targets # noch nötig?
        for k in range(num_targets):
            # !! unboundedness is only properly represented by None with optlang; using inifinity may cause trouble !!
            dual_lb = [None] * self.num_reac # optlang interprets None as Inf
            #dual_lb = numpy.full(self.num_reac, numpy.NINF)
            dual_ub = [None] * self.num_reac
            #dual_ub = numpy.full(self.num_reac, numpy.inf) # can lead to GLPK crash when trying to otimize an infeasible MILP
            # GLPK treats infinity different than declaring unboundedness explicitly by glp_set_col_bnds ?!?
            # could use numpy arrays and convert them to lists where None replaces inf before calling optlang
            if split_reversible_v:
                    for i in range(self.num_reac):
                        if irrev_geq or reversible[i]:
                            dual_lb[i] = 0
            else:
                if irrev_geq:
                    for i in range(self.num_reac):
                        if irr[i]:
                            dual_lb[i] = 0
                for i in irrepressible:
                    if reversible[i]:
                        dual_lb[i] = 0
            for i in irrepressible:
                dual_ub[i] = 0
            if split_reversible_v:
                dual_vars[k] = [self.optlang_variable_class("DP"+str(k)+"_"+str(i), lb=dual_lb[i], ub=dual_ub[i]) for i in range(self.num_reac)] + \
                    [self.optlang_variable_class("DN"+str(k)+"_"+str(i), ub=0) for i in split_v_idx]
                for i in irrepressible:
                    if reversible[i]: # fixes DN of irrepressible reversible reactions to 0
                         dual_vars[k][dual_rev_neg_idx_map[i]].lb = 0
            else:
                dual_vars[k] = [self.optlang_variable_class("DR"+str(k)+"_"+str(i), lb=dual_lb[i], ub=dual_ub[i]) for i in range(self.num_reac)]
            first_w= len(dual_vars[k]) # + 1;
            if use_kn_in_dual is False:
                dual = numpy.eye(self.num_reac)
                if split_reversible_v:
                    dual = numpy.hstack((dual, dual[:, split_v_idx]))
                #st_T_part = numpy.hstack((st.transpose(), targets[k][0].transpose()))
                #dual = numpy.hstack((dual, st_T_part))
                dual = numpy.hstack((dual, st.transpose(), targets[k][0].transpose()))
                #print(dual)
                dual_vars[k] += [self.optlang_variable_class("DS"+str(k)+"_"+str(i)) for i in range(st.shape[0])]
                first_w += st.shape[0]
            else:
                pass
    #       switch split_level
    #         case 1 % split dual vars associated with reversible reactions
    #           dual= [kn', kn(~irr, :)', kn'*T{k}'];
    #         case 2 % split all dual vars which are associated with reactions into DN <= 0, DP >= 0
    #           dual= [kn', kn', kn'*T{k}'];
    #         otherwise % no splitting
    #           dual= [kn', kn'*T{k}'];
    #       end
            dual_vars[k] += [self.optlang_variable_class("DT"+str(k)+"_"+str(i), lb=0) for i in range(targets[k][0].shape[0])]
            self.model.add(dual_vars[k])
            num_dual_cols[k]= dual.shape[1]
            constr= [None] * (dual.shape[0]+1)
            print(dual_vars[k][first_w:])
            for i in range(dual.shape[0]):
                if irrev_geq and irr[i]:
                    ub = None
                else:
                    ub = 0
                expr = add([cf * var for cf, var in zip(dual[i, :], dual_vars[k]) if cf != 0])
                #print(expr)
                constr[i] = self.optlang_constraint_class(expr, lb=0, ub=ub, name="D"+str(k)+"_"+str(i), sloppy=True)
                #print(constr[i])
            expr = add([cf * var for cf, var in zip(targets[k][1], dual_vars[k][first_w:]) if cf != 0])
            constr[-1] = self.optlang_constraint_class(expr, ub=-threshold, name="DW"+str(k), sloppy=True)
            self.model.add(constr)

            # constraints for the target(s) (cuts and knock-ins)
            if bigM > 0:
                for i in range(self.num_reac):
                    if cuts[i]:
                        self.model.add(self.optlang_constraint_class(dual_vars[k][i] - bigM*z_local[k][i],
                                       ub=0, name=z_local[k][i].name+dual_vars[k][i].name))
                        if reversible[i]:
                            if split_reversible_v:
                                dn = dual_vars[k][dual_rev_neg_idx_map[i]]
                            else:
                                dn = dual_vars[k][i]
                            self.model.add(self.optlang_constraint_class(dn + bigM*z_local[k][i],
                                           lb=0, name=z_local[k][i].name+dn.name+"r"))

    #         if knock_in(i)
    #           lpfw.write_z_flux_link(obj.z_var_names{i}, dual_var_names{k}{i}, bigM, '<=');
    #           if ~irr(i)
    #             switch split_level
    #               case 1
    #                 dn= dual_var_names{k}{dual_rev_neg_idx_map(i)};
    #               case 2
    #                 dn= dual_var_names{k}{obj.num_reac+i};
    #               otherwise
    #                 dn= dual_var_names{k}{i};
    #             end
    #             lpfw.write_z_flux_link(obj.z_var_names{i}, dn, -bigM, '>=');
    #           end
    #         end
    #       end
            else: # indicators
                for i in range(self.num_reac):
                    if cuts[i]:
                        if split_reversible_v:
                            self.model.add(self.optlang_constraint_class(dual_vars[k][i], ub=0,
                                           indicator_variable=z_local[k][i], active_when=0,
                                           name=z_local[k][i].name+dual_vars[k][i].name))
                            if reversible[i]:
                                dn = dual_vars[k][dual_rev_neg_idx_map[i]]
                                self.model.add(self.optlang_constraint_class(dn, lb=0,
                                               indicator_variable=z_local[k][i], active_when=0,
                                               name=z_local[k][i].name+dn.name))
                        else:
                            if irr[i]:
                                lb = None
                            else:
                                lb = 0
                            self.model.add(self.optlang_constraint_class(dual_vars[k][i], lb=lb, ub=0,
                                           indicator_variable=z_local[k][i], active_when=0,
                                           name=z_local[k][i].name+dual_vars[k][i].name))

    #         if knock_in(i)
    #           fprintf(lpfw_fid, '%s = 1 -> %s <= 0\n', obj.z_var_names{i}, dual_var_names{k}{i});
    #           if ~irr(i)
    #             switch split_level
    #               case 1
    #                 dn= dual_var_names{k}{dual_rev_neg_idx_map(i)};
    #               case 2
    #                 dn= dual_var_names{k}{obj.num_reac+i};
    #               otherwise
    #                 dn= dual_var_names{k}{i};
    #             end
    #             fprintf(lpfw_fid, '%s = 1 -> %s >= 0\n', obj.z_var_names{i}, dn);
    #           end
    #         end
    #       end
    #     end % if bigM > 0
    #   end % for k= 1:num_targets
    
        self.evs_sz_lb = 0
        self.evs_sz = self.optlang_constraint_class(add(self.z_vars), lb=self.evs_sz_lb, name='evs_sz')
        self.model.add(self.evs_sz)
        self.model.update() # transfer the model to the solver

    def single_solve(self):
        status = self.model._optimize() # raw solve without any retries
        self.model._status = status # needs to be set when using _optimize
        #self.model.problem.parameters.reset() # CPLEX raw
        #self.model.problem.solve() # CPLEX raw
        #cplex_status = self.model.problem.solution.get_status() # CPLEX raw
        #self.model._status = optlang.cplex_interface._CPLEX_STATUS_TO_STATUS[cplex_status] # CPLEX raw
        #status = self.model.status
        #if self.model.problem.solution.get_status_string() == 'integer optimal solution': # CPLEX raw
        if status is optlang.interface.OPTIMAL or status is optlang.interface.FEASIBLE:
            print(self.model.objective.value)
            # coeff= tuple(round(zv.primal) for zv in self.z_vars) # tuple can be used as set element
            z_idx= tuple(i for zv, i in zip(self.z_vars, range(len(self.z_vars))) if round(zv.primal))
            if self.ref_set is not None and z_idx not in self.ref_set:
                print("Incorrect result")
                print([zv.primal for zv in self.z_vars])
                print([(n,v) for n,v in zip(self.model.problem.variables.get_names(), self.model.problem.solution.get_values()) if v != 0])
                self.write_lp_file('failed')
            #    raise
            #coeff= [round(zv.primal) for zv in self.z_vars]
            #coeff = numpy.zeros(len(self.z_vars), dtype=float64) # klappt noch nicht, ndarry geht aber auch nicht als Element einer Menge
            #for i in range(len(self.z_vars)):
            #    coeff[i] = round(self.z_vars[i])

            #return coeff
            return z_idx
        else:
            return None

    def add_exclusion_constraint(self, mcs):
        # for coeff:
        #expression = add([cf * var for cf, var in zip(mcs, self.z_vars) if cf != 0]) # mul instead of * does not work directly
        #ub = sum(mcs)-1;
        # for z_idx
        expression = add([self.z_vars[i] for i in mcs])
        ub = len(mcs) - 1
        self.model.add(self.optlang_constraint_class(expression, ub=ub, sloppy=True))

    def enumerate_mcs(self, max_mcs_size=numpy.inf, enum_method=1):
        all_mcs= [];
        continue_loop = True;
        while continue_loop and self.evs_sz_lb <= max_mcs_size:
            if enum_method == 1:
                mcs = self.single_solve()
                if self.model.status == 'optimal':
                    #ov = round(self.model.objective.value)
                    #if ov > e.evs_sz.lb: # increase lower bound of evs_sz constraint, but is this really always helpful?
                    #    e.evs_sz.lb = ov
                    #    print(ov)
                    if round(self.model.objective.value) > max_mcs_size:
                        print('MCS size limit exceeded, stopping enumeration.')
                        break
                    self.add_exclusion_constraint(mcs)
                    self.model.update() # needs to be done explicitly when using _optimize
                    all_mcs.append(mcs)
                else:
                    break
            elif enum_method == 2: # populate with CPLEX
                # throw error if this is not a CPLEX model
                if numpy.isinf(max_mcs_size):
                    max_mcs_size = len(self.z_vars)
                print("Populate up tp MCS size ", max_mcs_size)
                self.model.problem.parameters.mip.pool.intensity.set(4)
                self.model.problem.parameters.mip.pool.absgap.set(0)
                self.model.problem.parameters.mip.strategy.search.set(1) # traditional branch-and-cut search
                # also set model.problem.parameters.parallel to deterministic?
                # for now unlimited pool size
                self.model.problem.parameters.mip.limits.populate.set(self.model.problem.parameters.mip.pool.capacity.get())
                while self.evs_sz_lb <= max_mcs_size:
                    self.evs_sz.ub = self.evs_sz_lb
                    self.evs_sz.lb = self.evs_sz_lb
                    try:
                        self.model.problem.populate_solution_pool()
                    except CplexSolverError:
                        print("Exception raised during populate")
                        continue_loop = False
                        break
                    print(self.model.problem.solution.pool.get_num())
                    print(self.model.problem.solution.get_status_string())
                    cplex_status = self.model.problem.solution.get_status()
                    if cplex_status is SolutionStatus.MIP_optimal:
                        self.evs_sz_lb += 1
                        print(self.evs_sz_lb)
                        z_idx = self.model.problem.variables.get_indices([z.name for z in self.z_vars])
                        for i in range(self.model.problem.solution.pool.get_num()):
                            mcs = tuple(numpy.where(numpy.round(
                                        self.model.problem.solution.pool.get_values(i, z_idx)))[0])
                            self.add_exclusion_constraint(mcs)
                            all_mcs.append(mcs)
                        self.model.update() # needs to be done explicitly when using _optimize
                    elif cplex_status is SolutionStatus.MIP_infeasible:
                        print('No MCS of size ', self.evs_sz_lb)
                        self.evs_sz_lb += 1
                    else:
                        print('Unexpected CPLEX status ', self.model.problem.solution.get_status_string())
                        continue_loop = False
                        break # provisional break
                # reset parameters here?
            else:
                print("Unknown enumeration method.")
                break

        print(self.model.status)
        return all_mcs

    def write_lp_file(self, fname):
        fname = fname + r".lp"
        if isinstance(self.model, optlang.cplex_interface.Model):
            self.model.problem.write(fname)
        elif isinstance(self.model, optlang.glpk_interface.Model):
            glp_write_lp(self.model.problem, None, fname)
        else:
            raise # add a proper exception here

def equations_to_matrix(model, equations):
    # add option to use names instead of ids
    # allow equations to be a list of lists
    dual = cobra.Model()
    reaction_ids = [r.id for r in model.reactions]
    dual.add_metabolites([cobra.Metabolite(r) for r in reaction_ids])
    for i in range(len(equations)):
        r = cobra.Reaction("R"+str(i)) 
        dual.add_reaction(r)
        r.build_reaction_from_string('=> '+equations[i])
    dual = cobra.util.array.create_stoichiometric_matrix(dual, array_type='DataFrame')
    if numpy.all(dual.index.values == reaction_ids):
        return dual.values.transpose()
    else:
        raise RuntimeError("Index order was not preserved.")

def expand_mcs(mcs, subT):
    #mcs = [[numpy.where(m)[0]] for m in mcs] # list of lists; mcs[i] will contain a list of MCS expanded from it
    mcs = [[list(m)] for m in mcs] # list of lists; mcs[i] will contain a list of MCS expanded from it
    rxn_in_sub = [numpy.where(subT[:, i])[0] for i in range(subT.shape[1])]
    for i in range(len(mcs)):
        num_iv = len(mcs[i][0]) # number of interventions in this MCS
        for s_idx in range(num_iv): # subset index
            for j in range(len(mcs[i])):
                rxns = rxn_in_sub[mcs[i][j][s_idx]]
                mcs[i][j][s_idx] = rxns[0]
                for k in range(1, len(rxns)):
                    mcs[i].append(mcs[i][j].copy())
                    mcs[i][-1][s_idx] = rxns[k]
    mcs = list(itertools.chain(*mcs))
    return set(map(tuple, map(numpy.sort, mcs)))
