"""Infer evolutionary trees by using various methods"""
import logging
import os
import numpy as np
from subprocess import call
from phylogeny.simple_phylogeny import SimplePhylogeny
from phylogeny.max_lh_phylogeny import MaxLHPhylogeny
from utils.mutation_matrix import write_mutation_matrix
from utils.data_tables import write_mutation_patterns
import plots.tikz_tree as tikz
import utils.latex_output as latex

__author__ = 'jreiter'
__date__ = 'July 11, 2015'


# get logger for application
logger = logging.getLogger('treeomics')


def infer_max_compatible_tree(filepath, patient, drivers=set(), time_limit=None):
    """
    Create an evolutionary tree where most conflicting mutations have been ignored
    due to the ambiguous binary classification of variants being present/absent
    :param filepath: tree is writen to the given file
    :param patient: data structure around the patient
    :param drivers: known driver genes in considered cancer type
    :param time_limit: time limit for MILP solver in seconds
    :return: evolutionary tree as graph
    """

    phylogeny = SimplePhylogeny(patient, patient.mps)

    # infer the tree which as to ignore the least number of mutation
    # to derive a conflict-free tree
    simple_tree = phylogeny.find_max_compatible_tree(time_limit=time_limit)

    # number of mutations inferred to be present in at least one sample
    no_present_muts = len(phylogeny.compatible_mutations)+len(phylogeny.conflicting_mutations)
    caption = ('Phylogenetic tree illustrating the clonal evolution of cancer. ' +
               'The derivation of an evolutionary conflict-free tree required the exclusion ' +
               'of {} out of {} ({:.1%}) mutations.'.format(
                 len(phylogeny.conflicting_mutations), no_present_muts,
                 float(len(phylogeny.conflicting_mutations)) / no_present_muts))

    # create tikz figure
    tikz.create_figure_file(simple_tree, tikz.TREE_ROOT, filepath,
                            patient, phylogeny, caption, drivers=drivers, standalone=True)
    # add information about the ignored mutations and the position of the acquired mutations
    latex.add_branch_mut_info(filepath, phylogeny, simple_tree)

    # ensure that the derived tree has the correct number of mutations on all leaves
    if logging.DEBUG == logger.getEffectiveLevel():
        for sa_idx, sa_name in enumerate(patient.sample_names):
            logger.debug("Compatible mutations present in sample {}: {}, {}".format(sa_name,
                         sum(1 for mut in patient.samples[sa_idx] if mut in phylogeny.compatible_mutations),
                         ', '.join(str(mut) for mut in patient.samples[sa_idx]
                                   if mut in phylogeny.compatible_mutations)))

        #     assert (len(pers_tree.node[frozenset([sa_idx])]['muts'])
        #             >= sum(1 for mut in patient.samples[sa_idx] if mut in phylogeny.compatible_mutations)), \
        #         'Mutations are incorrect for sample {}: {} != {}'.format(sa_idx,
        #         len(pers_tree.node[frozenset([sa_idx])]['muts']),
        #         sum(1 for mut in patient.samples[sa_idx] if mut in phylogeny.compatible_mutations))
        #
        # assert (sum(len(pers_tree[v1][v2]['muts']) for (v1, v2) in pers_tree.edges_iter())
        #         == len(phylogeny.compatible_mutations)), \
        #     'Total number of acquired mutations equals the number of compatible mutations: {} == {}'.format(
        #         sum(len(pers_tree[v1][v2]['muts']) for (v1, v2) in pers_tree.edges_iter()),
        #         len(phylogeny.compatible_mutations))

    return phylogeny


def create_max_lh_tree(patient, tree_filepath=None, mm_filepath=None, mp_filepath=None, subclone_detection=False,
                       loh_frequency=0.0, drivers=set(), max_no_mps=None, time_limit=None, plots=True,
                       pool_size=0, no_bootstrap_samples=0):
    """
    Create an evolutionary tree based on the maximum likelihood mutation patterns of each variant
    :param patient: data structure around the patient
    :param tree_filepath: tree is written to the given file
    :param mm_filepath: path to mutation matrix output file
    :param mp_filepath: path to mutation pattern output file
    :param subclone_detection: is subclone detection enabled?
    :param loh_frequency: probability that a SNV along a lineage is lost due loss of heterozygosity
    :param drivers: set of putative driver gene names highlighted on each edge
    :param max_no_mps: only the given maximal number of most likely (by joint likelihood) mutation patterns
            is explored per variant; limits the solution space
    :param time_limit: time limit for MILP solver in seconds
    :param plots: generate pdf from tex file
    :param pool_size: number of best solutions explored by ILP solver to estimate confidence
    :param no_bootstrap_samples: number of samples with replacement for the bootstrapping
    :return: evolutionary tree as graph
    """

    mlh_pg = MaxLHPhylogeny(patient, patient.mps, loh_frequency=loh_frequency)

    mlh_tree = mlh_pg.infer_max_lh_tree(subclone_detection=subclone_detection, max_no_mps=max_no_mps,
                                        pool_size=pool_size, time_limit=time_limit,
                                        no_bootstrap_samples=no_bootstrap_samples)

    if mlh_tree is not None:

        # ignore mutations which are not in any sample which passed the filtering
        present_mutations = patient.present_mutations

        no_fps = sum(len(fps) for mut_idx, fps in mlh_pg.false_positives.items())
        no_fns = sum(len(fns) for mut_idx, fns in mlh_pg.false_negatives.items())
        classification_info = ('Putative false-positives {}, put. false-negatives {}, put. false neg.-unknowns {}. '
                               .format(no_fps, no_fns,
                                       sum(len(fns) for mut_idx, fns in mlh_pg.false_negative_unknowns.items())))
        if mlh_pg.conflicting_mutations is not None:
            compatibility_info = ('{} ({:.1%})'.format(
                len(mlh_pg.conflicting_mutations), float(len(mlh_pg.conflicting_mutations)) /
                (len(mlh_pg.max_lh_mutations) + len(mlh_pg.conflicting_mutations))) +
                ' variants were evolutionarily incompatible due to the limited search space.')
        else:
            compatibility_info = ''
        logger.info(classification_info)

        caption = ('Phylogenetic tree illustrating the evolutionary history of the cancer. ' +
                   'The derivation of an evolutionarily-compatible maximum likelihood tree identified ' +
                   '{} putative false-positives or false-negatives (out of {}; {:.1%}). '.format(
                    no_fps+no_fns, len(patient.sample_names) * len(present_mutations),
                    float(no_fps+no_fns) / (len(patient.sample_names) * len(present_mutations))) +
                   classification_info + compatibility_info)

        # calculate median of number of persistent and present mutations inferred
        # in the evolutionary trajectory
        if len(mlh_pg.patient.variants.values()) > 0:
            median_no_muts = np.median([len(muts) for muts in mlh_pg.patient.variants.values()])
        else:
            median_no_muts = float('nan')

        if plots and tree_filepath is not None:

            try:
                from plots.ete_tree import create_tree
                mlh_pg.tree_plot = create_tree(mlh_tree, tikz.TREE_ROOT, tree_filepath, patient, mlh_pg,
                                               drivers=drivers)
                logger.info('Generated ete tree as PNG: {}'.format(mlh_pg.tree_plot))

            except ImportError as ie:
                logger.warn('ImportError! ete3 is not installed! {}'.format(ie))

            tikz_tree = tikz.create_figure_file(
                mlh_tree, tikz.TREE_ROOT, tree_filepath+'_full.tex', patient, mlh_pg, caption, drivers=drivers,
                germline_distance=10.0*max(1.0, len(mlh_pg.mlh_founders)/median_no_muts), standalone=True)
            # add information about the ignored mutations and the position of the acquired mutations
            latex.add_branch_mut_info(tree_filepath, mlh_pg, mlh_tree)

            # add information about the resolved mutation positions
            # which are likely sequencing errors
            latex.add_artifact_info(tree_filepath, mlh_pg)

            tikz_path, tikz_file = os.path.split(tikz_tree)
            logger.debug('Tikzpath: {} {}'.format(tikz_path, tikz_file))
            pdflatex_cmd = 'pdflatex {}'.format(tikz_file)
            fnull = open(os.devnull, 'w')
            return_code = call(pdflatex_cmd, shell=True, cwd=tikz_path, stdout=fnull)

            if return_code == 0:
                pdf_tree = tikz_tree.replace('.tex', '.pdf')
                logger.info('Successfully called pdflatex to create pdf of the evolutionary tree at {}'.format(
                    pdf_tree))
            else:
                logger.error('PDF of the evolutionary tree was not created. Is Latex/tikz installed?')

        # create mutation matrix for benchmarking
        if mm_filepath is not None:
            write_mutation_matrix(mlh_pg, mm_filepath)

        if mp_filepath is not None:
            write_mutation_patterns(mlh_pg, mp_filepath)

    else:
        logger.warn('Conflicts could not be resolved. No evolutionary tree has been created.')

    return mlh_pg
